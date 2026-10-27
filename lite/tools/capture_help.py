"""Capture the full app's help windows as Lite shows them.

Like the other capture scripts (throwaway settings, the real theme manager, a
real main window), with the application name and version Lite shows: the User
Guide window on Lite's guide (``lite/crates/callisto-lite/assets/user_guide.html``,
from ``make_user_guide.py``) at the top, at a section and after a search, with
where Qt's text layout put each block; Cite this Software; Report a Bug, empty
and filled; About; the update check's boxes (available, up to date, failed),
the download progress dialog and the downloaded box; and the Copied box.

usage: python lite/tools/capture_help.py <out_dir> <light|dark> [state ...]
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
only = set(sys.argv[3:])
out_dir.mkdir(parents=True, exist_ok=True)

LITE_NAME = "e-CALLISTO FITS Analyzer Lite"
import re  # noqa: E402

# Lite's own version (lite/Cargo.toml), as its windows show it.
LITE_VERSION = re.search(r'^version\s*=\s*"([^"]+)"', (REPO / "lite" / "Cargo.toml").read_text(encoding="utf-8"), re.M).group(1)
GUIDE = (REPO / "lite" / "crates" / "callisto-lite" / "assets" / "user_guide.html").read_text(encoding="utf-8")
GUIDE = GUIDE.replace("{{VERSION}}", LITE_VERSION)

for key in (
    "E_CALLISTO_DISABLE_GEOMAGNETIC_AUTOFETCH",
    "E_CALLISTO_DISABLE_EVENT_CONTEXT_AUTOFETCH",
    "E_CALLISTO_DISABLE_RECOVERY_PROMPT",
    "E_CALLISTO_DISABLE_SAVE_PROMPT",
):
    os.environ[key] = "1"
scratch = Path(tempfile.mkdtemp(prefix="callisto_help_"))
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

from PySide6.QtGui import QIcon, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QWidget  # noqa: E402

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setWindowIcon(QIcon(str(REPO / "assets" / "branding" / "icon.ico")))

import src.ui.help.user_guide_dialog as guide_dialog  # noqa: E402
from src.ui.app.main_window import MainWindow  # noqa: E402
from src.ui.common.mpl_style import apply_origin_style  # noqa: E402
from src.ui.common.theme_manager import AppTheme  # noqa: E402
from src.ui.help.bug_report_dialog import BugReportDialog  # noqa: E402
from src.ui.help.citation_dialog import CitationDialog  # noqa: E402

guide_dialog.USER_GUIDE_HTML = GUIDE
guide_dialog.APP_NAME = LITE_NAME

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


def capture(name: str, widget, extra: dict | None = None) -> None:
    widget.show()
    pump(0.8)
    if only and name not in only:
        return
    widget.grab().save(str(out_dir / f"{name}_{theme_mode}.png"))
    info = {"title": widget.windowTitle(), "size": [widget.width(), widget.height()],
            "dpr": widget.devicePixelRatioF(), "layout": dialog_layout(widget)}
    info.update(extra or {})
    (out_dir / f"{name}_{theme_mode}.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    print("captured", name)


def box(icon, title: str, text: str, buttons=QMessageBox.StandardButton.Ok, parent=None) -> QMessageBox:
    """The box `QMessageBox.information(parent, title, text)` and its kin build."""
    return QMessageBox(icon, title, text, buttons, parent or win)


# ------------------------------------------------------------------ user guide

def guide_blocks(browser) -> list[dict]:
    """Where Qt's text layout put each block, in document coordinates."""
    doc = browser.document()
    layout = doc.documentLayout()
    rows = []
    block = doc.begin()
    while block.isValid():
        r = layout.blockBoundingRect(block)
        fmt = block.charFormat()
        rows.append({"text": block.text()[:60], "rect": [r.x(), r.y(), r.width(), r.height()],
                     "lines": block.layout().lineCount(), "px": round(fmt.font().pointSizeF() * 96 / 72, 2)})
        block = block.next()
    return rows


def guide_extra(dlg) -> dict:
    b = dlg.browser
    return {"scroll": b.verticalScrollBar().value(), "scroll_max": b.verticalScrollBar().maximum(),
            "viewport": [b.viewport().width(), b.viewport().height()],
            "document_size": [b.document().size().width(), b.document().size().height()],
            "document_margin": b.document().documentMargin(),
            "selection": b.textCursor().selectedText(),
            "blocks": guide_blocks(b)}


win.open_user_guide()
guide = win._user_guide_dialog
guide.move(80, 60)
capture("window_user_guide", guide, guide_extra(guide))
guide.browser.scrollToAnchor("menu-file")
capture("window_user_guide_section", guide, guide_extra(guide))
guide.go_home()
guide.find_edit.setText("ruler")
guide.find_next()
capture("window_user_guide_find", guide, guide_extra(guide))
guide.find_next()
capture("window_user_guide_find_next", guide, guide_extra(guide))
not_found = box(QMessageBox.Icon.Information, "Find", '"burst ridge" was not found.', parent=guide)
capture("box_guide_not_found", not_found)
not_found.close()
guide.close()
pump(0.3)

# ---------------------------------------------------------------------- citation

cite = CitationDialog(parent=win)
capture("dialog_cite", cite)
copied = box(QMessageBox.Icon.Information, "Copied", "Citation text copied to clipboard.", parent=cite)
capture("box_copied", copied)
copied.close()
cite.close()

# -------------------------------------------------------------------- bug report

bug = BugReportDialog(repo="SaanDev/e-Callisto_FITS_Analyzer", context_provider=lambda: {}, parent=win)
capture("dialog_bug_report", bug)
bug.title_edit.setText("Crash when exporting PNG")
bug.details_edit.setPlainText("1. Opened a file\n2. Pressed Ctrl+E\n3. The app closed")
bug._bundle_path = str(Path("C:/Users/me/Downloads/bug_report_20261021_101500.zip"))
bug._update_diag_label()
capture("dialog_bug_report_filled", bug)
bug.close()

# ------------------------------------------------------------------------- about

# QMessageBox.about: an Information box showing the window icon at 64 x 64.
about = box(QMessageBox.Icon.Information, f"About {LITE_NAME}",
            f"{LITE_NAME} version {LITE_VERSION}.\n\n"
            "Developed by Sahan S Liyanage\n\n"
            "Astronomical and Space Science Unit\n"
            "University of Colombo, Sri Lanka\n\n"
            "Website: https://ecallistoanalyzer.org\n\n"
            "2026©Copyright, All Rights Reserved.")
about_icon = about.windowIcon()
about.setIconPixmap(about_icon.pixmap(about_icon.actualSize(QtCore.QSize(64, 64))))
capture("box_about", about)
about.close()

# ---------------------------------------------------------------------- updates

available = QMessageBox(win)
available.setWindowTitle("Update Available")
available.setIcon(QMessageBox.Information)
available.setText(f"A newer version of {LITE_NAME} is available.")
available.setInformativeText(f"Current version: v{LITE_VERSION}\nLatest version: v3.3.0-lite\nPublished: 2026-11-02")
available.setDetailedText("What's New\n- Faster loading of combined files\n- Fixes for the Type II window")
available.addButton("Download", QMessageBox.AcceptRole)
available.addButton("Release Page", QMessageBox.ActionRole)
available.addButton(QMessageBox.Close)
capture("box_update_available", available)
for b in available.buttons():
    if b.text().startswith("Show Details"):
        b.click()
pump(0.3)
capture("box_update_available_details", available)
available.close()

up_to_date = box(QMessageBox.Icon.Information, "Up to Date", f"You are using the latest version.\n\nCurrent: v{LITE_VERSION}")
capture("box_up_to_date", up_to_date)
up_to_date.close()
failed = box(QMessageBox.Icon.Warning, "Update Check Failed",
             "Could not check for updates.\n\nNetwork error while checking updates: timed out")
capture("box_update_failed", failed)
failed.close()

progress = QProgressDialog("Downloading update...", "Cancel", 0, 0, win)
progress.setWindowTitle("Downloading Update")
progress.setMinimumDuration(0)
progress.setAutoClose(False)
progress.setAutoReset(False)
progress.setValue(0)
capture("dialog_update_download", progress)
progress.setRange(0, 52_428_800)
progress.setValue(19_922_944)
progress.setLabelText("Downloading update... 38.0% (19.0 MB / 50.0 MB)")
capture("dialog_update_download_progress", progress)
progress.close()

downloaded = QMessageBox(win)
downloaded.setWindowTitle("Update Downloaded")
downloaded.setIcon(QMessageBox.Information)
downloaded.setText("The update was downloaded successfully.")
downloaded.setInformativeText("Saved to:\nC:\\Users\\me\\Downloads\\e-CALLISTO_FITS_Analyzer_v3.3.0-lite_Setup.exe\n\n"
                              "Close the app and run this installer/package to complete the update.")
downloaded.addButton("Open File", QMessageBox.ActionRole)
downloaded.addButton("Open Folder", QMessageBox.ActionRole)
downloaded.addButton(QMessageBox.Ok)
capture("box_update_downloaded", downloaded)
downloaded.close()

# The status bar's update label in each state.
for state, text in (("checking", "Updates: checking..."), ("available", "Updates: v3.3.0-lite available"),
                    ("current", "Updates: up to date"), ("failed", "Updates: check failed")):
    win._set_update_status_label(text)
    pump(0.2)
    lbl = win.update_status_label
    p = lbl.mapTo(win, QtCore.QPoint(0, 0))
    (out_dir / f"status_update_{state}_{theme_mode}.json").write_text(json.dumps(
        {"text": text, "rect": [p.x(), p.y(), lbl.width(), lbl.height()]}, indent=1), encoding="utf-8")

# ----------------------------------------------------------------------- splash

from src.ui.app.startup_loading import StartupLoadingScreen  # noqa: E402

splash = StartupLoadingScreen(app_name=LITE_NAME, version=LITE_VERSION,
                              logo_path=str(REPO / "assets" / "branding" / "FITS_analyzer.png"))
splash.present()
for value, text in ((15, "Loading analysis modules..."), (45, "Applying interface styling..."),
                    (80, "Preparing the main workspace..."), (100, "Opening analyzer...")):
    splash.set_progress(value, text)
    capture(f"splash_{value}", splash)
splash.dismiss()

win.close()
