"""Capture the full app's data-tool windows: Batch FIT Processing and the
multi-station comparison with its export dialog.

Like ``capture_analysis.py`` (throwaway settings, the real theme manager, a
real main window): loads a parity fixture into the main window, opens each
window from it, drives it through fixed states and saves, for each state, the
window, its layout (rectangles, fonts, size hints) and its texts. Batch runs
write into a scratch folder; one output PNG is kept next to the captures.

usage: python lite/tools/capture_tools.py <out_dir> <light|dark> [state ...]
"""

from __future__ import annotations

import json
import os
import shutil
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
scratch = Path(tempfile.mkdtemp(prefix="callisto_tools_"))
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
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget  # noqa: E402

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


def capture_dialog(name: str, widget, extra: dict | None = None, grab=None) -> None:
    widget.show()
    pump(0.8)
    if only and name not in only:
        if grab is not None:
            grab(widget)
        return
    (grab(widget) if grab is not None else widget.grab()).save(str(out_dir / f"{name}_{theme_mode}.png"))
    info = {"title": widget.windowTitle(), "size": [widget.width(), widget.height()],
            "layout": dialog_layout(widget)}
    info.update(extra or {})
    (out_dir / f"{name}_{theme_mode}.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    print("captured", name)


win.load_fits_into_main(str(FIX / "INDIA-GAURI_20261007_041500_02.fit.gz"))
pump(1.0)

# ------------------------------------------------------------ batch processing

win.open_batch_processing_window()
batch = win._batch_processing_dialog
capture_dialog("dialog_batch", batch)

batch_in = scratch / "batch_in"
batch_in.mkdir()
for name in ("BIR_20261007_101459_01.fit.gz", "INDIA-GAURI_20261007_041500_02.fit.gz", "GERMANY-DLR_20261007_050000_01.fit.gz"):
    shutil.copy(FIX / name, batch_in / name)
(batch_in / "notes.txt").write_text("not a FIT file", encoding="utf-8")
batch_out = scratch / "batch_out"
batch.input_dir_edit.setText(str(batch_in))
batch.output_dir_edit.setText(str(batch_out))
batch.raw_output_radio.setChecked(True)
capture_dialog("dialog_batch_raw", batch)
batch.background_output_radio.setChecked(True)
batch.background_method_combo.setCurrentIndex(2)
batch.colormap_combo.setCurrentText("inferno")
batch.use_current_range_chk.setChecked(True)
capture_dialog("dialog_batch_filled", batch)

# Run it: the summary box is recorded instead of shown.
summaries = []


def _record(kind):
    def box(parent, title, text, *args, **kwargs):
        summaries.append({"kind": kind, "title": title, "text": text})
        return QMessageBox.StandardButton.Ok
    return box


real_boxes = (QMessageBox.information, QMessageBox.warning, QMessageBox.critical)
QMessageBox.information, QMessageBox.warning, QMessageBox.critical = _record("information"), _record("warning"), _record("critical")
try:
    batch.use_current_range_chk.setChecked(False)
    batch._start_batch()
    deadline = time.time() + 120
    while batch.is_running() and time.time() < deadline:
        pump(0.2)
    pump(0.5)
    capture_dialog("dialog_batch_done", batch, {"summaries": summaries})
finally:
    QMessageBox.information, QMessageBox.warning, QMessageBox.critical = real_boxes
outputs = sorted(p.name for p in batch_out.glob("*.png"))
(out_dir / f"batch_outputs_{theme_mode}.json").write_text(json.dumps({"outputs": outputs, "summaries": summaries}, indent=1), encoding="utf-8")
for name in outputs:
    shutil.copy(batch_out / name, out_dir / f"batch_png_{theme_mode}_{name}")
batch.close()
pump(0.3)

# ------------------------------------------------------ multi-station comparison

from dataclasses import replace  # noqa: E402

from src.backend.session.measurements import calculate_two_point_measurement  # noqa: E402
from src.ui.radio.dialogs.comparison_export_dialog import (  # noqa: E402
    EXPORT_LAYOUT_GRID,
    ComparisonExportDialog,
    ComparisonExportOptions,
)


def comparison_extra(dlg) -> dict:
    """What the layout dump does not show: the list, combos, panels."""
    def combo_items(combo):
        return [combo.itemText(i) for i in range(combo.count())]
    panels = []
    for widget, key in zip(dlg._hardware_canvases, dlg._visible_panel_order):
        state = dlg._visible_panel_states.get(key)
        p = widget.mapTo(dlg, QtCore.QPoint(0, 0))
        payload = state.payload if state else None
        panels.append({
            "rect": [p.x(), p.y(), widget.width(), widget.height()],
            "visible": widget.isVisible(),
            "label": payload.dataset.label if payload else "",
            "levels": list(payload.levels) if payload else None,
            "pg_extent": list(payload.pg_extent) if payload else None,
        })
    from PySide6.QtWidgets import QScrollArea
    scrolls = []
    for area in dlg.findChildren(QScrollArea):
        b = area.verticalScrollBar()
        p = area.mapTo(dlg, QtCore.QPoint(0, 0))
        scrolls.append({"rect": [p.x(), p.y(), area.width(), area.height()], "bar": [b.value(), b.maximum(), b.pageStep()],
                        "bar_visible": b.isVisible(), "content": [area.widget().width(), area.widget().height()]})
    return {
        "files": [dlg.file_list.item(i).text() for i in range(dlg.file_list.count())],
        "status": dlg.status_label.text(),
        "readout": dlg.measurement_readout.text(),
        "noise_targets": combo_items(dlg.noise_target_combo),
        "alignment": dlg.current_alignment_mode(),
        "panels": panels,
        "scroll_areas": scrolls,
    }


def grab_comparison(dlg):
    """`dlg.grab()` with each panel's pyqtgraph scene rendered in, clipped
    to the plot scroll area (an OpenGL viewport grabs blank)."""
    from PySide6.QtCore import QRect, QRectF
    from PySide6.QtGui import QImage, QPainter
    shot = dlg.grab()
    dpr = dlg.devicePixelRatioF()
    viewport = dlg.hardware_scroll.viewport()
    clip = QRect(viewport.mapTo(dlg, QtCore.QPoint(0, 0)), viewport.size())
    for widget in dlg._hardware_canvases:
        gv = widget._graphics
        img = QImage(int(round(gv.width() * dpr)), int(round(gv.height() * dpr)), QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(gv.backgroundBrush().color())
        painter = QPainter(img)
        gv.scene().render(painter, QRectF(0, 0, gv.width(), gv.height()), gv.mapToScene(gv.viewport().rect()).boundingRect())
        painter.end()
        painter = QPainter(shot)
        painter.setClipRect(clip)
        painter.drawImage(gv.mapTo(dlg, QtCore.QPoint(0, 0)), img)
        painter.end()
    return shot


def capture_comparison(name: str, dlg, more: dict | None = None) -> None:
    pump(0.6)
    capture_dialog(name, dlg, {**comparison_extra(dlg), **(more or {})}, grab=grab_comparison)


win.open_multi_station_comparison_dialog()
cmp_dlg = win._multi_station_comparison_dialog
pump(0.8)
# It maximizes itself when first shown; the captures use a fixed size.
cmp_dlg.showNormal()
cmp_dlg.resize(1400, 900)
pump(0.5)
capture_comparison("window_comparison_one", cmp_dlg)

four = ["GERMANY-DLR_20261007_050000_01.fit.gz", "TRIEST_20261007_050000_57.fit.gz",
        "INDIA-GAURI_20261007_050000_02.fit.gz", "AUSTRIA-OE3FLB_20261007_051500_55.fit.gz"]
cmp_dlg.clear_files()
cmp_dlg.add_files([str(FIX / n) for n in four])
capture_comparison("window_comparison_four", cmp_dlg)

cmp_dlg.add_files([str(FIX / "GERMANY-DLR_20261007_050000_02.fit.gz"), str(FIX / "TRIEST_20261007_050000_59.fit.gz")])
capture_comparison("window_comparison_combined", cmp_dlg)

# Noise clipping on every panel.
cmp_dlg.noise_method_combo.setCurrentIndex(4)
pump(0.3)
cmp_dlg.noise_low_slider.setValue(1800)
cmp_dlg.noise_high_slider.setValue(2600)
capture_comparison("window_comparison_clip", cmp_dlg)

# Back to no noise reduction; per-station scales in dB, inferno.
cmp_dlg.noise_method_combo.setCurrentIndex(0)
cmp_dlg.units_combo.setCurrentIndex(1)
cmp_dlg.colormap_combo.setCurrentText("inferno")
cmp_dlg.color_scale_combo.setCurrentIndex(1)
capture_comparison("window_comparison_per_station", cmp_dlg)

# A measurement on the second panel, at fixed fractions of its extent.
cmp_dlg.color_scale_combo.setCurrentIndex(0)
cmp_dlg.units_combo.setCurrentIndex(0)
cmp_dlg.colormap_combo.setCurrentText("Custom")
pump(0.4)
key = cmp_dlg._visible_panel_order[1]
x0, x1, y0, y1 = cmp_dlg._visible_panel_states[key].payload.pg_extent
lo, hi = min(y0, y1), max(y0, y1)
p1 = (x0 + 0.20 * (x1 - x0), lo + 0.75 * (hi - lo))
p2 = (x0 + 0.45 * (x1 - x0), lo + 0.30 * (hi - lo))
cmp_dlg._set_measurement_result(key, calculate_two_point_measurement(p1, p2))
pump(0.3)
extra_points = {"measurement_panel": 1, "measurement_points": [list(p1), list(p2)]}
capture_comparison("window_comparison_measure", cmp_dlg, extra_points)
cmp_dlg.clear_ruler_measurements()
pump(0.3)

# The export dialog, both layouts.
options = replace(ComparisonExportOptions(), title=cmp_dlg._default_grid_export_title())
export_dlg = ComparisonExportDialog(initial_options=options, parent=cmp_dlg)
capture_dialog("dialog_comparison_export", export_dlg)
export_dlg.grid_radio.setChecked(True)
capture_dialog("dialog_comparison_export_grid", export_dlg)
export_dlg.close()

# Both exports of the combined comparison.
exports = scratch / "comparison_out"
exports.mkdir()
grid_options = replace(options, layout=EXPORT_LAYOUT_GRID)
cmp_dlg._export_grid_plot(str(exports / "grid.png"), "png", grid_options)
cmp_dlg._export_visible_plot(str(exports / "visible.png"), "png")
for f in ("grid.png", "visible.png"):
    shutil.copy(exports / f, out_dir / f"comparison_export_{theme_mode}_{f}")
(out_dir / f"comparison_export_{theme_mode}.json").write_text(json.dumps(
    {"title": options.title, "dpi": grid_options.dpi}, indent=1), encoding="utf-8")
cmp_dlg.close()
pump(0.3)

win.close()
