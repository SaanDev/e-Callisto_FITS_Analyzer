"""Capture reference screenshots of the full app's empty main window and menus.

The window is built the way ``src/main.py`` builds it (Fusion style, AppTheme,
modern view mode), with every QSettings redirected to a throwaway INI file so the
user's real settings are never read or written.

usage: python lite/tools/capture_reference.py <out_dir> <light|dark> [width height]
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

out_dir = Path(sys.argv[1])
theme_mode = sys.argv[2]
width, height = (int(sys.argv[3]), int(sys.argv[4])) if len(sys.argv) > 4 else (1600, 1000)
out_dir.mkdir(parents=True, exist_ok=True)

for key in (
    "E_CALLISTO_DISABLE_GEOMAGNETIC_AUTOFETCH",
    "E_CALLISTO_DISABLE_EVENT_CONTEXT_AUTOFETCH",
    "E_CALLISTO_DISABLE_RECOVERY_PROMPT",
    "E_CALLISTO_DISABLE_SAVE_PROMPT",
):
    os.environ[key] = "1"
scratch = Path(tempfile.mkdtemp(prefix="callisto_ref_"))
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

from PySide6.QtGui import QFontMetrics, QGuiApplication, QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
app.setStyle("Fusion")
icon_path = REPO / "assets" / "branding" / "icon.ico"
if icon_path.exists():
    app.setWindowIcon(QIcon(str(icon_path)))

from src.ui.app.main_window import MainWindow  # noqa: E402
from src.ui.common.mpl_style import apply_origin_style  # noqa: E402
from src.ui.common.theme_manager import AppTheme  # noqa: E402

MainWindow._prompt_recovery_if_needed = lambda self: None
# Same order as src/main.py: style, theme, then register the theme on the app,
# which is where MainWindow and the dialogs look it up.
apply_origin_style()
theme = AppTheme(app)
app.setProperty("theme_manager", theme)
win = MainWindow(theme=theme)
win.resize(width, height)
win.move(40, 40)
win.show()


def pump(seconds: float = 1.5) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


pump(2.5)
screen = QGuiApplication.primaryScreen()
info = {
    "theme": theme_mode,
    "logical_size": [win.width(), win.height()],
    "device_pixel_ratio": screen.devicePixelRatio(),
    "logical_dpi": screen.logicalDotsPerInch(),
    "font": app.font().family(),
    "font_point_size": app.font().pointSizeF(),
    "font_pixel_size": QFontMetrics(app.font()).height(),
    "menus": [],
}

win.grab().save(str(out_dir / f"main_{theme_mode}.png"))
# The whole sidebar, including the cards below the fold.
win.side_scroll.widget().grab().save(str(out_dir / f"sidebar_{theme_mode}.png"))


def _widget_text(w):
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


from PySide6.QtWidgets import QWidget  # noqa: E402

layout = []
for w in [win, *win.findChildren(QWidget)]:
    if not w.isVisibleTo(win) or w.width() <= 0 or w.height() <= 0:
        continue
    top_left = w.mapTo(win, QtCore.QPoint(0, 0)) if w is not win else QtCore.QPoint(0, 0)
    font = w.font()
    layout.append(
        {
            "class": type(w).__name__,
            "name": w.objectName(),
            "text": _widget_text(w),
            "rect": [top_left.x(), top_left.y(), w.width(), w.height()],
            "enabled": w.isEnabled(),
            "font": [font.family(), font.pixelSize() if font.pixelSize() > 0 else round(font.pointSizeF() * 96 / 72, 2), font.weight()],
        }
    )
(out_dir / f"layout_{theme_mode}.json").write_text(json.dumps(layout, indent=1), encoding="utf-8")

def _menu_items(menu):
    """Every item of a menu, recursing into submenus (Qt fills some on show)."""
    try:
        menu.aboutToShow.emit()
    except Exception:
        pass
    items = []
    for a in menu.actions():
        sub = a.menu()
        items.append(
            {"text": a.text(), "separator": a.isSeparator(), "enabled": a.isEnabled(),
             "checkable": a.isCheckable(), "checked": a.isChecked(),
             "submenu": _menu_items(sub) if sub is not None else None,
             "shortcut": a.shortcut().toString()}
        )
    return items


for action in win.menuBar().actions():
    menu = action.menu()
    if menu is None:
        continue
    rect = win.menuBar().actionGeometry(action)
    menu.popup(win.menuBar().mapToGlobal(rect.bottomLeft()))
    pump(0.6)
    name = action.text().replace(" ", "_").lower()
    menu.grab().save(str(out_dir / f"menu_{name}_{theme_mode}.png"))
    info["menus"].append(
        {
            "title": action.text(),
            "bar_rect": [rect.x(), rect.y(), rect.width(), rect.height()],
            "popup_size": [menu.width(), menu.height()],
            "item_rects": [
                [g.x(), g.y(), g.width(), g.height()]
                for g in (menu.actionGeometry(a) for a in menu.actions())
            ],
            "items": _menu_items(menu),
        }
    )
    # Submenus: show each next to its parent item, as hovering would.
    for sub_action in menu.actions():
        sub = sub_action.menu()
        if sub is None:
            continue
        geo = menu.actionGeometry(sub_action)
        sub.popup(menu.mapToGlobal(geo.topRight()))
        pump(0.4)
        sub_name = sub_action.text().replace(" ", "_").replace("/", "_").lower()
        sub.grab().save(str(out_dir / f"submenu_{name}__{sub_name}_{theme_mode}.png"))
        sub.hide()
        pump(0.1)
    menu.hide()
    pump(0.2)

# Text metrics for calibrating the Rust UI's font sizes against Qt's.
from PySide6.QtGui import QFont  # noqa: E402

samples = [
    ("Background Subtraction", 15, 600),
    ("Noise Clipping Thresholds", 15, 600),
    ("Method", 12, 400),
    ("Logarithmic Threshold Scale", 12, 400),
    ("Load a FITS file to subtract its background.", 11, 400),
    ("Frequency scale", 10, 700),
    ("0.00 Digits", 13, 700),
    ("Updates: not checked", 12, 400),
    ("Cite this Software", 12, 400),
]
metrics = []
for text, px, weight in samples:
    f = QFont(app.font().family())
    f.setPixelSize(px)
    f.setWeight(QFont.Weight(weight))
    fm = QFontMetrics(f)
    metrics.append(
        {"text": text, "px": px, "weight": weight, "advance": fm.horizontalAdvance(text),
         "ascent": fm.ascent(), "descent": fm.descent(), "height": fm.height()}
    )
info["font_metrics"] = metrics

(out_dir / f"reference_{theme_mode}.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
print(json.dumps({k: v for k, v in info.items() if k != "menus"}))
win.close()
