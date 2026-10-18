"""Capture the full app's download and import windows in named states.

Like ``capture_states.py``: throwaway settings, the real theme manager and real
dialogs. The network is replaced by canned answers built from the parity
fixtures (station lists, a day listing, the FITS files themselves, a burst
catalog, a synthetic Learmonth day), so every state is reproducible and nothing
is downloaded. The download caches point into a scratch folder.

For each state it saves the window image and its widget layout.

usage: python lite/tools/capture_downloader.py <out_dir> <light|dark> [state ...]
"""

from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
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
scratch = Path(tempfile.mkdtemp(prefix="callisto_dl_"))

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

from PySide6.QtCore import QDate, QDateTime, QPoint, QTime, Qt  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication, QProgressDialog, QWidget  # noqa: E402

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setWindowIcon(QIcon(str(REPO / "assets" / "branding" / "icon.ico")))

from src.backend.radio import burst_list as bl  # noqa: E402
from src.backend.radio import callisto_cache  # noqa: E402
from src.ui.common.mpl_style import apply_origin_style  # noqa: E402
from src.ui.common.theme_manager import AppTheme  # noqa: E402
from src.ui.downloads import archive_stations, callisto_downloader as cd, learmonth_downloader as ld  # noqa: E402
from src.ui.radio import burst_fits as bf, burst_list_tab as blt  # noqa: E402

apply_origin_style()
theme = AppTheme(app)
app.setProperty("theme_manager", theme)

# ------------------------------------------------------------------ the fake network

DAY = date(2026, 10, 7)
DAY_FILES = sorted(p.name for p in FIX.glob("*.fit.gz"))
EXTRA = ["ALASKA-ANCHORAGE_20261007_001500_01.fit.gz", "Malaysia_Banting_20261007_000000_59.fit.gz",
         "BIR_20261007_103000_01.fit.gz", "BIR_20261007_104500_01.fit.gz", "BIR_20261007_101500_02.fit.gz"]
LISTING = "<html><body><table>" + "".join(
    f'<tr><td><a href="{n}">{n}</a></td></tr>' for n in sorted(DAY_FILES + EXTRA)
) + "</table></body></html>"
STATIONS = sorted({cd.parse_callisto_archive_filename(n)[0] for n in DAY_FILES + EXTRA}, key=str.casefold)


class FakeResponse:
    def __init__(self, status: int, body: bytes):
        self.status_code = status
        self.content = body
        self.text = body.decode("utf-8", "replace")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code} Client Error")

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i:i + chunk_size]


class FakeSession:
    headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    def head(self, url, **kwargs):
        return FakeResponse(200, b"")

    def get(self, url, **kwargs):
        name = url.rstrip("/").rsplit("/", 1)[-1]
        if (FIX / name).is_file():
            return FakeResponse(200, (FIX / name).read_bytes())
        if url.endswith("/2026/10/07/"):
            return FakeResponse(200, LISTING.encode())
        if url.endswith("/"):
            return FakeResponse(200, b"<html></html>")
        return FakeResponse(404, b"")


cache_dir = scratch / "callisto_cache"
callisto_cache.cache_root = lambda: cache_dir
archive_stations.fetch_stations_for_day = lambda day: list(STATIONS) if day == DAY else []
for module in (cd, bf, callisto_cache):
    if hasattr(module, "build_archive_session"):
        module.build_archive_session = FakeSession
cd.check_archive_server = lambda client=None: (True, "")
_real_fetch_cached = callisto_cache.fetch_cached


def _fetch_cached(url, filename="", *, session=None, progress_cb=None, **kwargs):
    return _real_fetch_cached(url, filename, session=FakeSession(), progress_cb=progress_cb)


callisto_cache.fetch_cached = _fetch_cached
blt.fetch_burst_events = lambda start, end, progress=None, cancelled=None: bl.BurstListResult(
    events=bl.parse_burst_list(
        "#Date\tTime\tType\tStations\n"
        "20261007\t04:20-04:22\tIII\tINDIA-GAURI, GERMANY-DLR\n"
        "20261007\t05:05-05:09\tII\tGERMANY-DLR, AUSTRIA-OE3FLB, (TRIEST)\tslow drift\n"
        "20261007\t10:16-10:16\tIII\tBIR\n"
        "20261007\t00:05-00:20\tCTM\tMEXART, SWISS-IRSOL\n",
        source_url="https://soleil.i4ds.ch/solarradio/data/BurstLists/2010-yyyy_Monstein/2026/e-CALLISTO_2026_10.txt",
    ),
    warnings=["No published burst catalog for 2026 month(s): 09."],
)

lm_cache = scratch / "learmonth_cache"
lm_cache.mkdir()
ld._default_learmonth_cache_dir = lambda: lm_cache


def _learmonth_day(day, cache_dir, session=None):
    path = Path(cache_dir) / f"{day:%y}" / f"LM{day:%y%m%d}.srs"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        buf = bytearray()
        start = datetime(day.year, day.month, day.day, 0, 0, 0)
        for i in range(1200):
            t = start + timedelta(seconds=3 * i)
            buf += bytes([t.year - 2000, t.month, t.day, t.hour, t.minute, t.second, 3, 0])
            buf += struct.pack(">HH4s", 25, 75, b"\0" * 4) + struct.pack(">HH4s", 75, 180, b"\0" * 4)
            buf += bytes((i * 7 + k) % 256 for k in range(802))
        path.write_bytes(bytes(buf))
    return str(path)


ld.download_learmonth_day = _learmonth_day

# ------------------------------------------------------------------ helpers


def pump(seconds: float = 0.6) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_for(condition, timeout: float = 60.0) -> None:
    end = time.time() + timeout
    while time.time() < end and not condition():
        app.processEvents()
        time.sleep(0.02)
    pump(0.5)


def widget_text(w):
    for attr in ("text", "title", "currentText", "placeholderText", "windowTitle"):
        fn = getattr(w, attr, None)
        if callable(fn):
            try:
                value = fn()
            except TypeError:
                continue
            if isinstance(value, str) and value:
                return value
    return ""


def layout_dump(top):
    rows = []
    for w in [top, *top.findChildren(QWidget)]:
        if not w.isVisibleTo(top) or w.width() <= 0 or w.height() <= 0:
            continue
        p = w.mapTo(top, QPoint(0, 0)) if w is not top else QPoint(0, 0)
        font = w.font()
        sp = w.sizePolicy()
        cap = lambda v: min(v, 99999)
        extra = {}
        from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTreeView
        if isinstance(w, QAbstractItemView) and hasattr(w, "model") and w.model() is not None:
            m = w.model()
            hh = w.header() if isinstance(w, QTreeView) else getattr(w, "horizontalHeader", lambda: None)()
            if hh is not None:
                extra["sections"] = [hh.sectionSize(i) for i in range(hh.count())]
            if isinstance(w, QTreeView):
                extra["row_heights"] = [w.visualRect(m.index(r, 0)).height() for r in range(min(m.rowCount(), 3))]
                extra["indentation"] = w.indentation()
            elif hasattr(w, "rowHeight"):
                extra["row_heights"] = [w.rowHeight(r) for r in range(min(m.rowCount(), 3))]
            else:
                extra["row_heights"] = [w.sizeHintForRow(r) for r in range(min(m.rowCount(), 3))]
            extra["viewport"] = list(w.viewport().geometry().getRect())
        rows.append({"class": type(w).__name__, "name": w.objectName(), "text": widget_text(w), **extra,
                     "rect": [p.x(), p.y(), w.width(), w.height()], "enabled": w.isEnabled(),
                     "font": [font.family(), round(font.pointSizeF() * 96 / 72, 2), font.weight()],
                     "hint": [w.sizeHint().width(), w.sizeHint().height()],
                     "min_hint": [w.minimumSizeHint().width(), w.minimumSizeHint().height()],
                     "min": [w.minimumWidth(), w.minimumHeight()], "max": [cap(w.maximumWidth()), cap(w.maximumHeight())],
                     "policy": [sp.horizontalPolicy().name, sp.verticalPolicy().name, sp.horizontalStretch(),
                                sp.verticalStretch(), sp.hasHeightForWidth()],
                     "advance": w.fontMetrics().horizontalAdvance(widget_text(w)),
                     "fm": [w.fontMetrics().height(), w.fontMetrics().ascent(), w.fontMetrics().averageCharWidth(),
                            w.fontMetrics().horizontalAdvance("x")],
                     "margins": list(w.contentsMargins().__reduce__()[1]) if False else
                                [w.contentsMargins().left(), w.contentsMargins().top(),
                                 w.contentsMargins().right(), w.contentsMargins().bottom()]})
    return rows


def capture(name: str, widget) -> None:
    pump(0.8)
    if only and name not in only:
        return
    widget.grab().save(str(out_dir / f"{name}_{theme_mode}.png"))
    info = {"title": widget.windowTitle(), "size": [widget.width(), widget.height()],
            "screen": list(widget.screen().availableGeometry().getRect()), "layout": layout_dump(widget)}
    (out_dir / f"{name}_{theme_mode}.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    print("captured", name)


def capture_popup(name: str, popup, anchor) -> None:
    """A popup window, with where it opened relative to the field it drops from."""
    pump(0.8)
    if only and name not in only:
        return
    popup.grab().save(str(out_dir / f"{name}_{theme_mode}.png"))
    a = anchor.mapToGlobal(QPoint(0, 0))
    g = popup.geometry()
    info = {"title": popup.windowTitle(), "size": [popup.width(), popup.height()],
            "anchor": [a.x(), a.y(), anchor.width(), anchor.height()],
            "offset": [g.x() - a.x(), g.y() - a.y()], "layout": layout_dump(popup)}
    (out_dir / f"{name}_{theme_mode}.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    print("captured", name)


# ------------------------------------------------------------------ the e-CALLISTO downloader

dlg = cd.CallistoDownloaderApp()
qday = QDate(DAY.year, DAY.month, DAY.day)
dlg.date_edit.setDate(qday)
dlg.overview_date_edit.setDate(qday)
dlg.event_start_dt_edit.setDateTime(QDateTime(qday, QTime(4, 0, 0), Qt.UTC))
dlg.event_stop_dt_edit.setDateTime(QDateTime(qday, QTime(6, 0, 0), Qt.UTC))
dlg.burst_list_tab.start_date_edit.setDate(QDate(2026, 9, 7))
dlg.burst_list_tab.end_date_edit.setDate(qday)
dlg.show()
wait_for(lambda: dlg.station_dropdown.count() > 0)
capture("dl_single", dlg)

# The date edit's calendar and the station list, open.
from PySide6.QtTest import QTest  # noqa: E402

QTest.mouseClick(dlg.date_edit, Qt.LeftButton, Qt.NoModifier,
                 QPoint(dlg.date_edit.width() - 12, dlg.date_edit.height() // 2))
wait_for(lambda: dlg.calendar_popup.isVisible(), timeout=5)
capture_popup("popup_calendar", dlg.calendar_popup.window(), dlg.date_edit)
dlg.calendar_popup.window().hide()
dlg.station_dropdown.showPopup()
wait_for(lambda: dlg.station_dropdown.view().isVisible(), timeout=5)
capture_popup("popup_combo", dlg.station_dropdown.view().window(), dlg.station_dropdown)
dlg.station_dropdown.hidePopup()
pump(0.3)

dlg.station_dropdown.setCurrentText("INDIA-GAURI")
dlg.show_available_fits()
wait_for(lambda: dlg.file_tree.topLevelItemCount() > 0)
dlg.file_tree.topLevelItem(0).child(1).setCheckState(0, Qt.Checked)
capture("dl_single_files", dlg)

dlg.tabs.setCurrentIndex(1)
wait_for(lambda: dlg.event_station_list.count() > 0)
capture("dl_event", dlg)
for row in range(dlg.event_station_list.count()):
    item = dlg.event_station_list.item(row)
    if item.text() in ("INDIA-GAURI", "GERMANY-DLR"):
        item.setCheckState(Qt.Checked)
dlg.search_event_fits()
wait_for(lambda: dlg.event_results_table.rowCount() > 0)
capture("dl_event_results", dlg)

dlg.tabs.setCurrentIndex(2)
capture("dl_overview", dlg)
dlg.overview_station_dropdown.setCurrentText("INDIA-GAURI")
dlg.generate_spectral_overview()
wait_for(lambda: dlg._overview_thread is None and dlg.overview_preview_tabs.count() > 0, 180)
capture("dl_overview_result", dlg)

dlg.tabs.setCurrentIndex(3)
capture("dl_burst", dlg)
tab = dlg.burst_list_tab
tab.load_events()
wait_for(lambda: not tab.is_busy() and tab.catalog_table.rowCount() > 0)
tab.catalog_table.selectRow(1)
pump(0.3)
tab.find_fits()
wait_for(lambda: not tab.is_busy() and tab.fits_table.rowCount() > 0)
capture("dl_burst_loaded", dlg)

# Preview windows: one file, and a mixed selection that gives several panels.
for name, files in (("preview_single", ["BIR_20261007_101459_01.fit.gz"]),
                    ("preview_multi", ["INDIA-GAURI_20261007_041500_02.fit.gz", "INDIA-GAURI_20261007_043000_02.fit.gz",
                                       "BIR_20261007_101459_01.fit.gz"])):
    panels, _errors = cd.build_preview_panels([(str(FIX / f), f) for f in files])
    title = panels[0].get("title", "") if len(panels) == 1 else f"{len(panels)} panels"
    window = cd.PreviewWindow(panels, title, parent=dlg)
    window.show()
    capture(name, window)
    window.close()

# The import progress dialog, as MainWindow._show_import_progress_dialog makes it.
progress = QProgressDialog("Downloading selected FITS files...", "", 0, 3, dlg)
progress.setWindowTitle("Importing FITS Files")
progress.setWindowModality(Qt.ApplicationModal)
progress.setCancelButton(None)
progress.setMinimumDuration(0)
progress.setAutoClose(False)
progress.setAutoReset(False)
progress.setValue(1)
progress.show()
capture("import_progress", progress)
progress.close()
dlg.tabs.setCurrentIndex(0)
dlg.reject()

# ------------------------------------------------------------------ Learmonth

lmd = ld.LearmonthDownloaderApp()
lmd.date_edit.setDate(QDate(2026, 10, 7))
lmd.time_edit.setTime(QTime(0, 20, 0))
lmd.show()
capture("learmonth", lmd)
lmd.show_available_data()
wait_for(lambda: lmd.chunk_list.count() > 0)
lmd.chunk_list.item(0).setCheckState(Qt.Checked)
capture("learmonth_loaded", lmd)
lmd.reject()
