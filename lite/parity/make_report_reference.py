"""Write the full app's project reports, for Lite's report tests.

Opens the projects ``make_project_reference.py`` wrote (raw, processed,
analysis, combined) in a real main window (throwaway settings and caches)
and generates each one's project report the way File → Generate Project
Report does, minus the Event Story (Lite has no event context: the story is
stubbed out, as when nothing can be told). The generation time is fixed so
the reports can be compared.

For each project it writes ``<name>_report.pdf``, ``<name>_report.json``
(the report's input: every table's rows, the figures' titles, notes and
captions, their PNG sizes) and ``<name>_report_layout.json``: where
reportlab put every flowable (page, position, size), each paragraph's lines
and each table's column widths and row heights.

usage: python lite/parity/make_report_reference.py   (after make_project_reference.py)
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
OUT = REPO / "lite" / "parity" / "out" / "projects"
GENERATED_AT = "2026-10-21T09:00:00+05:30"

for key in (
    "E_CALLISTO_DISABLE_GEOMAGNETIC_AUTOFETCH",
    "E_CALLISTO_DISABLE_EVENT_CONTEXT_AUTOFETCH",
    "E_CALLISTO_DISABLE_RECOVERY_PROMPT",
    "E_CALLISTO_DISABLE_SAVE_PROMPT",
):
    os.environ[key] = "1"
scratch = Path(tempfile.mkdtemp(prefix="callisto_reports_"))
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

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
app.setStyle("Fusion")

import src.ui.app.report_story as report_story  # noqa: E402
from reportlab.platypus import flowables  # noqa: E402
from src.backend.session.project_report import generate_project_report_pdf  # noqa: E402
from src.ui.app.main_window import MainWindow  # noqa: E402
from src.ui.common.mpl_style import apply_origin_style  # noqa: E402
from src.ui.common.theme_manager import AppTheme  # noqa: E402

MainWindow._prompt_recovery_if_needed = lambda self: None
# Lite has no event context: no story is told.
report_story.story_request = lambda main, session: None
apply_origin_style()
theme = AppTheme(app)
app.setProperty("theme_manager", theme)
win = MainWindow(theme=theme)
win.resize(1600, 1000)
win.show()


def pump(seconds: float = 1.0) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


# ------------------------------------------------------------ the recorder

LAYOUT: list[dict] = []
_offsets: list[tuple[float, float]] = [(0.0, 0.0)]
_real_draw_on = flowables.Flowable.drawOn


def _lines(p) -> list[str]:
    bl = getattr(p, "blPara", None)
    if bl is None:
        return []
    out = []
    for line in bl.lines:
        if bl.kind == 0:
            out.append(" ".join(line[1]))
        else:
            out.append("".join(getattr(w, "text", "") for w in getattr(line, "words", [])))
    return out


def recording_draw_on(self, canvas, x, y, _sW=0):
    x = self._hAlignAdjust(x, _sW)
    ox, oy = _offsets[-1]
    item = {"page": canvas.getPageNumber(), "kind": type(self).__name__, "x": ox + x, "y": oy + y,
            "width": float(getattr(self, "width", 0) or 0), "height": float(getattr(self, "height", 0) or 0)}
    if item["kind"] == "Paragraph":
        item.update(style=self.style.name, lines=_lines(self))
    elif item["kind"] == "Image":
        item.update(width=float(self.drawWidth), height=float(self.drawHeight))
    elif item["kind"] == "Table":
        item.update(colWidths=[float(v) for v in self._colWidths], rowHeights=[float(v) for v in self._rowHeights])
    elif item["kind"] == "Preformatted":
        item.update(lines=list(self.lines))
    LAYOUT.append(item)
    _offsets.append((ox + x, oy + y))
    try:
        _real_draw_on(self, canvas, x, y, 0)
    finally:
        _offsets.pop()


flowables.Flowable.drawOn = recording_draw_on


def plain(value):
    """The report input as JSON."""
    import numpy as np

    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, np.ndarray):
        return plain(value.tolist())
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


for name in ("raw", "processed", "analysis", "combined"):
    win._close_analysis_windows()
    assert win.open_project_path(str(OUT / f"{name}.efaproj")), name
    pump(1.5)
    report = win._build_project_report_input()
    report.generated_at = GENERATED_AT
    LAYOUT.clear()
    path = OUT / f"{name}_report.pdf"
    result = generate_project_report_pdf(str(path), report)
    (OUT / f"{name}_report_layout.json").write_text(json.dumps(LAYOUT, indent=1, ensure_ascii=False), encoding="utf-8")
    figures = []
    for i, fig in enumerate(report.figures):
        size = None
        if fig.png_bytes:
            from PIL import Image
            import io

            size = list(Image.open(io.BytesIO(fig.png_bytes)).size)
            (OUT / f"{name}_report_figure{i}.png").write_bytes(fig.png_bytes)
        figures.append({"title": fig.title, "source_filename": fig.source_filename, "caption": fig.caption,
                        "availability_note": fig.availability_note, "png_size": size})
    data = {
        "title": report.title, "app": report.app, "data_source": report.data_source, "processing": report.processing,
        "rfi": report.rfi, "annotations": report.annotations, "light_curve": report.light_curve,
        "analysis_session": report.analysis_session, "analysis_row": report.analysis_row,
        "project_path": report.project_path, "fits_primary": report.fits_primary, "station": report.station,
        "date_obs": report.date_obs, "selected_header": dict(report.selected_header),
        "full_header": report.full_header, "figures": figures, "pages": None, "figures_written": result.figures_written,
    }
    (OUT / f"{name}_report.json").write_text(json.dumps(plain(data), indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    pages = max((item["page"] for item in LAYOUT), default=0)
    print(name, "report:", pages, "pages,", result.figures_written, "figures,", result.file_size, "bytes")
