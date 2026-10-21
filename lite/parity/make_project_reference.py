"""Write projects with the full app, for Lite's project tests.

Drives a real main window (throwaway settings and caches, as the capture
scripts do) into a few states and saves each as an ``.efaproj``:

* ``raw``: one fixture as loaded;
* ``processed``: background subtracted, clipping thresholds moved, dB units,
  UT time axis, another colormap, Graph Properties, a zoomed view, a light
  curve, and a polygon, a line and a text annotation;
* ``analysis``: the burst isolated with the lasso, the maximum intensities,
  the Analyzer's best fit and Type II band splitting with fits and results;
* ``combined``: two consecutive fixtures combined in time.

Each project is then opened again and saved as ``<name>.roundtrip.efaproj``:
what the full app itself writes back after reading it, which Lite has to
match. ``<name>.open.json`` records the window title, the status line and
the analysis windows the reopened project showed. Two more files test
reading only: ``legacy.efaproj`` (schema 1, analysis in the legacy
``max_intensity`` keys) and ``passthrough.efaproj`` (``processed`` with parts
Lite does not have, which it must write back unchanged).

usage: python lite/parity/make_project_reference.py [out_dir]
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
FIX = REPO / "lite" / "parity" / "fixtures"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "lite" / "parity" / "out" / "projects"
if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)

for key in (
    "E_CALLISTO_DISABLE_GEOMAGNETIC_AUTOFETCH",
    "E_CALLISTO_DISABLE_EVENT_CONTEXT_AUTOFETCH",
    "E_CALLISTO_DISABLE_RECOVERY_PROMPT",
    "E_CALLISTO_DISABLE_SAVE_PROMPT",
):
    os.environ[key] = "1"
scratch = Path(tempfile.mkdtemp(prefix="callisto_projects_"))
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
seed.setValue("ui/theme_mode", "light")
seed.setValue("ui/view_mode", "modern")
seed.sync()

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
app.setStyle("Fusion")

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
win.show()
acc = win.accel_canvas


def pump(seconds: float = 1.0) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


class _Click:
    def __init__(self, pos, button, double=False):
        self._pos, self._button, self._double = pos, button, double

    def scenePos(self):
        return self._pos

    def button(self):
        return self._button

    def double(self):
        return self._double


class _SceneEvent:
    def __init__(self, kind, pos, button=Qt.MouseButton.NoButton):
        self._kind, self._pos, self._button = kind, pos, button

    def type(self):
        return self._kind

    def scenePos(self):
        return self._pos

    def button(self):
        return self._button


def scene_pos(x, y):
    return acc._viewbox.mapViewToScene(QPointF(float(x), acc._axis_y_scalar(float(y))))


def click(x, y, button=Qt.MouseButton.LeftButton):
    acc._on_scene_mouse_clicked(_Click(scene_pos(x, y), button))
    pump(0.05)


def scene_event(kind, x, y, button=Qt.MouseButton.NoButton):
    acc._handle_scene_event(_SceneEvent(kind, scene_pos(x, y), button))
    pump(0.02)


LEFT, RIGHT = Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton
GAURI = [str(FIX / "INDIA-GAURI_20261007_041500_02.fit.gz"), str(FIX / "INDIA-GAURI_20261007_043000_02.fit.gz")]
LASSO = [(100.0, 85.0), (300.0, 88.0), (500.0, 70.0), (520.0, 45.0), (350.0, 35.0), (150.0, 50.0)]
POLYGON = [(200.0, 80.0), (320.0, 82.0), (300.0, 60.0)]
LINE = [(500.0, 85.0), (700.0, 40.0)]
TEXT = (600.0, 70.0)


def save(name: str) -> None:
    pump(0.5)
    path = str(OUT / f"{name}.efaproj")
    assert win._write_project_to(path, "link"), name
    print("saved", name)


def reopen(name: str) -> None:
    """Open a project again and save what the full app writes back."""
    win._close_analysis_windows()
    assert win.open_project_path(str(OUT / f"{name}.efaproj")), name
    pump(1.5)
    opened = {
        "title": win.windowTitle(),
        "status": win.statusBar().currentMessage(),
        "max_window": win._dialog_alive(getattr(win, "_max_intensity_dialog", None)),
        "analyzer_window": win._dialog_alive(getattr(win, "_analyze_dialog", None)),
        "type_ii_window": win._dialog_alive(getattr(win, "_type_ii_dialog", None)),
    }
    (OUT / f"{name}.open.json").write_text(json.dumps(opened, indent=1), encoding="utf-8")
    assert win._write_project_to(str(OUT / f"{name}.roundtrip.efaproj"), "link"), name
    print("round trip", name)
    if name in ("raw", "processed", "combined"):
        # Export to FIT with the recommended BITPIX, to <name>_export.fit.
        import src.ui.app.main_window as mw
        real_item, real_save = mw.QInputDialog.getItem, mw.QFileDialog.getSaveFileName
        mw.QInputDialog.getItem = staticmethod(lambda *a, **k: (a[3][0], True))
        fit_path = str(OUT / f"{name}_export.fit")
        mw.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (fit_path, ""))
        try:
            win.export_to_fits()
        finally:
            mw.QInputDialog.getItem, mw.QFileDialog.getSaveFileName = real_item, real_save
    if name in ("processed", "analysis"):
        # Export Analysis Log, then Export Provenance Report, to <name>_export_*.
        import src.ui.app.main_window as mw
        target = str(OUT / f"{name}_export.csv")
        real = mw.QFileDialog.getSaveFileName
        mw.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (target, "CSV (*.csv)"))
        try:
            win.export_analysis_log()
            win.export_provenance_report()
        finally:
            mw.QFileDialog.getSaveFileName = real
    # Export Figure last (switching the axis to log is logged), to
    # <name>_export_figure.png (and, for processed, on a log frequency axis
    # too); the message boxes are only recorded.
    import src.ui.app.main_window as mw
    boxes = []
    real_pick, real_info, real_crit = win._pick_export_path_for_figure, mw.QMessageBox.information, mw.QMessageBox.critical
    mw.QMessageBox.information = staticmethod(lambda _p, title, text: boxes.append([title, text]))
    mw.QMessageBox.critical = staticmethod(lambda _p, title, text: boxes.append([title, text]))
    real_save_figure = mw.save_figure

    def recording_save_figure(fig, path, **kw):
        out = real_save_figure(fig, path, **kw)
        # The layout the save ended with: axes positions in figure fractions.
        boxes.append(["axes", [list(ax.get_position().bounds) for ax in fig.axes]])
        return out

    mw.save_figure = recording_save_figure
    try:
        figures = [(f"{name}_export_figure.png", False)]
        if name == "processed":
            figures += [("processed_export_figure.pdf", False), ("processed_export_figure.eps", False),
                        ("processed_log_export_figure.png", True)]
        for target, log in figures:
            win._pick_export_path_for_figure = lambda *a, _t=str(OUT / target), **k: (_t, _t.rsplit(".", 1)[1])
            if log:
                win.set_frequency_axis_scale("log")
                pump(0.5)
            win.export_figure()
            if log:
                win.set_frequency_axis_scale("linear")
                pump(0.5)
    finally:
        win._pick_export_path_for_figure = real_pick
        mw.save_figure = real_save_figure
        mw.QMessageBox.information, mw.QMessageBox.critical = real_info, real_crit
    (OUT / f"{name}_export_figure.json").write_text(json.dumps(boxes, indent=1), encoding="utf-8")


# ------------------------------------------------------------------ states

win.load_fits_into_main(GAURI[0])
pump(1.0)
save("raw")

win.background_method_combo.setCurrentIndex(0)
win.background_subtract_btn.click()
pump(0.5)
win.lower_slider.setValue(win.lower_slider.value() - 40)
win.upper_slider.setValue(win.upper_slider.value() + 60)
pump(0.8)
win.units_db_radio.setChecked(True)
win.time_ut_radio.setChecked(True)
win.cmap_combo.setCurrentText("inferno")
win.title_edit.setText("Burst over Gauribidanur")
win.title_bold_chk.setChecked(True)
win.tick_font_spin.setValue(13)
pump(0.5)
win.plot_light_curve_at_frequency(62.0)
win.start_annotation_polygon()
for x, y in POLYGON:
    click(x, y)
click(*POLYGON[-1], button=RIGHT)
win.start_annotation_line()
for x, y in LINE:
    click(x, y)
win._open_text_annotation_dialog = lambda **_kw: {
    "text": "Type III burst", "color": "#ffcc00", "font_family": "", "font_size": 16, "font_bold": True, "font_italic": False,
}
win.start_annotation_text()
click(*TEXT)
acc.set_view({"xlim": (120.0, 640.0), "ylim": (38.0, 86.0)})
save("processed")
# The preset of this state, as Save Current as Preset builds it.
from src.backend.session.presets import build_preset  # noqa: E402
PRESET = build_preset("Processed look", win._preset_settings_payload())
(OUT / "processed.preset.json").write_text(json.dumps(PRESET, indent=2, sort_keys=True), encoding="utf-8")
from src.backend.session.view_config import dump_view_config_json  # noqa: E402
(OUT / "processed.efaview.json").write_text(dump_view_config_json(win._build_view_config_payload()), encoding="utf-8")

# The analysis windows, as `capture_analysis.py` drives them.
win.load_fits_into_main(GAURI[0])
pump(1.0)
win.background_method_combo.setCurrentIndex(0)
win.background_subtract_btn.click()
pump(0.5)
win.activate_lasso()
scene_event(QEvent.Type.GraphicsSceneMousePress, *LASSO[0], LEFT)
for x, y in LASSO[1:]:
    scene_event(QEvent.Type.GraphicsSceneMouseMove, x, y)
scene_event(QEvent.Type.GraphicsSceneMouseRelease, *LASSO[-1], LEFT)
pump(0.5)
win.plot_max_intensities()
mx = win._max_intensity_dialog
pump(0.5)
mx.open_analyze_window()
an = win._analyze_dialog
an.plot_max()
an.plot_fit()
pump(0.5)
t2 = win._open_or_focus_type_ii_dialog()
pump(0.5)


def t2_click(x, y):
    pos = t2.plot_item.vb.mapViewToScene(QPointF(float(x), float(y)))
    t2._on_plot_clicked(_Click(pos, LEFT))
    pump(0.05)


t2.add_points_button.setChecked(True)
for x, y in [(300.0, 80.0), (340.0, 70.0), (380.0, 62.0), (420.0, 56.0)]:
    t2_click(x, y)
t2.band_combo.setCurrentIndex(1)
for x, y in [(300.0, 60.0), (340.0, 52.0), (380.0, 46.0), (420.0, 42.0)]:
    t2_click(x, y)
t2._fit_both_bands()
t2._calculate_parameters()
pump(0.5)
save("analysis")
win._close_analysis_windows()

win.load_combined_into_main(combine_compatible(GAURI))
pump(1.0)
save("combined")

for name in ("raw", "processed", "analysis", "combined"):
    reopen(name)

# The preset applied to the raw project, then saved.
win._close_analysis_windows()
assert win.open_project_path(str(OUT / "raw.efaproj"))
pump(1.0)
assert win._apply_preset_payload(PRESET)
pump(0.5)
assert win._write_project_to(str(OUT / "raw_with_preset.efaproj"), "link")
print("preset applied")

# ------------------------------------------------------- read-only variants


def rewrite(src: str, dst: str, edit) -> None:
    """Copy a project, changing its meta and arrays with ``edit``."""
    import numpy as np

    with zipfile.ZipFile(OUT / src) as zf:
        meta = json.loads(zf.read("meta.json"))
        with np.load(io.BytesIO(zf.read("arrays.npz")), allow_pickle=False) as npz:
            arrays = {k: npz[k] for k in npz.files}
        others = {n: zf.read(n) for n in zf.namelist() if n not in ("meta.json", "arrays.npz")}
    meta, arrays, others = edit(meta, arrays, others)
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    with zipfile.ZipFile(OUT / dst, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(meta, indent=2, sort_keys=True, ensure_ascii=False))
        zf.writestr("arrays.npz", buf.getvalue())
        for name, data in others.items():
            zf.writestr(name, data)


def legacy(meta, arrays, others):
    """A v2.5-era file: schema 1, no parts, the analysis in `max_intensity` only."""
    for key in ("parts", "storage", "project_id", "files", "analysis_session"):
        meta.pop(key, None)
    meta["schema_version"] = 1
    for key in [k for k in arrays if k.startswith(("analysis_", "type_ii_"))]:
        arrays.pop(key)
    return meta, arrays, others


def passthrough(meta, arrays, others):
    """Parts Lite does not have: their sections, arrays and embedded files."""
    import numpy as np

    meta["parts"].update({"solar": True, "geomagnetic": True})
    meta["storage"] = "embed"
    meta["solar"] = {"frames": [{"member": "solar/frames/0001_aia.fits", "time": "2026-10-07T04:20:00"}],
                     "zoom": [0.25, 1.5, None], "note": "Lite keeps this ☉"}
    meta["geomagnetic"] = {"station": "ABK", "visible": True, "limits": [-12.5, float("nan")]}
    arrays["solar_mask"] = np.arange(12, dtype=np.int16).reshape(3, 4)
    arrays["geomagnetic_h_nt"] = np.linspace(-3.0, 3.0, 7)
    others["solar/frames/0001_aia.fits"] = b"SIMPLE  =                    T" + bytes(range(256)) * 8
    meta["files"] = sorted(others)
    return meta, arrays, others


rewrite("analysis.efaproj", "legacy.efaproj", legacy)
rewrite("processed.efaproj", "passthrough.efaproj", passthrough)
win._close_analysis_windows()
reopen("legacy")
print("written to", OUT)
win.close()
