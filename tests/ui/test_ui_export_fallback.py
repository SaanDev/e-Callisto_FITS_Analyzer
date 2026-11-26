"""
e-CALLISTO FITS Analyzer
Version 3.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Export Figure: the main plot is saved as an OriginPro-style graph in light mode,
whichever canvas is showing it, and the save dialog keeps the chosen format.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")
pytest.importorskip("astropy")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

import src.ui.app.main_window as main_window_module
from src.ui.app.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def _loaded_window() -> MainWindow:
    win = MainWindow(theme=None)
    win.filename = "demo.fit"
    win.freqs = np.linspace(90.0, 20.0, 40)
    win.time = np.linspace(0.0, 900.0, 60)
    data = np.zeros((40, 60), dtype=float)
    data[10:30, 20:40] = 10.0
    win.raw_data = data
    return win


def test_export_figure_saves_an_origin_style_graph_in_light_mode(tmp_path: Path, monkeypatch):
    _app()
    win = _loaded_window()
    monkeypatch.setattr(win, "_is_dark_ui", lambda: True)
    out = tmp_path / "export"
    monkeypatch.setattr(win, "_pick_export_path_for_figure", lambda *args, **kwargs: (str(out), "PNG (*.png)"))
    messages = []
    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: messages.append(("info", args[2])))
    monkeypatch.setattr(main_window_module.QMessageBox, "critical", lambda *args, **kwargs: messages.append(("error", args[2])))

    win.export_figure()

    saved = tmp_path / "export.png"
    assert messages == [("info", f"Figure saved:\n{saved}")]
    image = QImage(str(saved))
    assert not image.isNull()
    assert image.pixelColor(0, 0) == QColor("#ffffff")
    win.close()


def test_export_figure_offers_every_publication_format(monkeypatch):
    _app()
    win = _loaded_window()
    offered = {}

    def fake_pick(caption, default_name, filters, default_filter=None):
        offered["filters"] = filters
        return "", ""

    monkeypatch.setattr(win, "_pick_export_path_for_figure", fake_pick)
    win.export_figure()

    assert offered["filters"] == main_window_module.FIGURE_EXPORT_FILTERS
    win.close()


def test_export_ignores_a_stale_view_that_misses_the_spectrum(monkeypatch):
    _app()
    win = _loaded_window()
    monkeypatch.setattr(win, "_hardware_mode_enabled", lambda: True)
    monkeypatch.setattr(win.accel_canvas, "get_view", lambda: {"xlim": (0.0, 1.0), "ylim": (0.0, 1.0)})
    monkeypatch.setattr(win, "_current_dynamic_spectrum_levels", lambda: (-5.0, -1.0))

    fig = win._build_origin_export_figure()

    ax = fig.axes[0]
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    assert x0 <= 0.0 and x1 >= 900.0
    assert min(y0, y1) <= 20.0 and max(y0, y1) >= 90.0
    assert ax.images[0].get_clim() == (0.0, 10.0), "levels that miss the data fall back to its range"
    win.close()


def test_hardware_export_reproduces_the_accelerated_view_and_overlays(monkeypatch):
    _app()
    win = _loaded_window()
    monkeypatch.setattr(win, "_hardware_mode_enabled", lambda: True)
    monkeypatch.setattr(win.accel_canvas, "get_view", lambda: {"xlim": (100.0, 400.0), "ylim": (30.0, 60.0)})
    win._annotations = [{"kind": "text", "points": [[150.0, 50.0]], "text": "Burst", "visible": True}]

    fig = win._build_origin_export_figure()

    ax = fig.axes[0]
    assert ax.get_xlim() == pytest.approx((100.0, 400.0))
    assert ax.get_ylim() == pytest.approx((30.0, 60.0))
    assert "Burst" in {text.get_text() for text in ax.texts}
    assert ax.get_title() == "demo.fit-Raw"
    assert all(spine.get_visible() for spine in ax.spines.values())
    assert ax.xaxis.get_tick_params()["top"] is True
    win.close()


def test_hardware_pick_export_path_appends_selected_filter_extension(monkeypatch):
    _app()
    win = MainWindow(theme=None)
    monkeypatch.setattr(win, "_hardware_mode_enabled", lambda: True)
    monkeypatch.setattr(main_window_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: ("/tmp/hardware-export", "PNG (*.png)"),
    )

    path, ext = win._pick_export_path_for_figure(
        "Export Figure",
        "hardware-export",
        "PNG (*.png);;PDF (*.pdf)",
        default_filter="PNG (*.png)",
    )

    assert path == "/tmp/hardware-export.png"
    assert ext == "png"
    win.close()


def test_linux_hardware_pick_export_path_uses_shared_helper(monkeypatch):
    _app()
    win = MainWindow(theme=None)
    monkeypatch.setattr(win, "_hardware_mode_enabled", lambda: True)
    monkeypatch.setattr(main_window_module.sys, "platform", "linux")

    called = {}

    def fake_pick_export_path(parent, caption, default_name, filters, default_filter=None):
        called["args"] = (caption, default_name, filters, default_filter)
        return "/tmp/hardware-export.pdf", "pdf"

    monkeypatch.setattr(main_window_module, "pick_export_path", fake_pick_export_path)
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("native dialog should not be used")),
    )

    path, ext = win._pick_export_path_for_figure(
        "Export Figure",
        "hardware-export",
        "PNG (*.png);;PDF (*.pdf)",
        default_filter="PNG (*.png)",
    )

    assert path == "/tmp/hardware-export.pdf"
    assert ext == "pdf"
    assert called["args"] == (
        "Export Figure",
        "hardware-export",
        "PNG (*.png);;PDF (*.pdf)",
        "PNG (*.png)",
    )
    win.close()


# A plot title holds the FITS file name, so the default export name does too:
# "….fit-Background Subtracted" must not be taken for the format.
_TITLE_NAME = "SWISS-FM_20261126_083847_02.fit-Background Subtracted"


def test_resolve_export_path_adds_the_format_to_a_title_name():
    from src.ui.common.gui_shared import resolve_export_path

    filters = main_window_module.FIGURE_EXPORT_FILTERS
    assert resolve_export_path(f"/tmp/{_TITLE_NAME}", "PNG (*.png)", filters) == (f"/tmp/{_TITLE_NAME}.png", "png")
    assert resolve_export_path(f"/tmp/{_TITLE_NAME}", "PDF (*.pdf)", filters) == (f"/tmp/{_TITLE_NAME}.pdf", "pdf")
    # No filter reported (some native dialogs): PNG.
    assert resolve_export_path("/tmp/burst.fit", "", filters) == ("/tmp/burst.fit.png", "png")
    # A format the filters offer is kept, as typed.
    assert resolve_export_path(f"/tmp/{_TITLE_NAME}.SVG", "PNG (*.png)", filters) == (f"/tmp/{_TITLE_NAME}.SVG", "svg")
    assert resolve_export_path("/tmp/plot.tif", "PNG (*.png)", filters) == ("/tmp/plot.tif", "tif")
    # Another dialog's filters: its own first format when PNG is not one of them.
    assert resolve_export_path("/tmp/movie.v2", "", "MP4 (*.mp4);;GIF (*.gif)") == ("/tmp/movie.v2.mp4", "mp4")
    assert resolve_export_path("/tmp/movie.gif", "MP4 (*.mp4)", "MP4 (*.mp4);;GIF (*.gif)") == ("/tmp/movie.gif", "gif")


def test_export_default_name_shows_the_format():
    from src.ui.common.gui_shared import export_default_name

    filters = main_window_module.FIGURE_EXPORT_FILTERS
    assert export_default_name(_TITLE_NAME, filters, "PNG (*.png)") == f"{_TITLE_NAME}.png"
    assert export_default_name("plot", filters, "PDF (*.pdf)") == "plot.pdf"
    assert export_default_name("plot.png", filters, "PDF (*.pdf)") == "plot.png"
    assert export_default_name("plot", filters) == "plot.png"


def test_pick_export_path_saves_a_title_name_as_png(monkeypatch):
    import src.ui.common.gui_shared as gui_shared

    _app()
    monkeypatch.setattr(gui_shared, "IS_LINUX", False)
    offered = {}

    def fake_save(parent, caption, name, filters, selected=""):
        offered["name"] = name
        # Accepted as offered, minus the suffix the dialog showed: as a portal
        # or a user editing the name would return it.
        return (f"/tmp/{_TITLE_NAME}", "PNG (*.png)")

    monkeypatch.setattr(gui_shared.QFileDialog, "getSaveFileName", fake_save)
    path, ext = gui_shared.pick_export_path(
        None, "Export Figure", _TITLE_NAME, main_window_module.FIGURE_EXPORT_FILTERS, default_filter="PNG (*.png)"
    )
    assert offered["name"] == f"{_TITLE_NAME}.png"
    assert (path, ext) == (f"/tmp/{_TITLE_NAME}.png", "png")


def test_hardware_pick_export_path_saves_a_title_name_as_png(monkeypatch):
    _app()
    win = MainWindow(theme=None)
    monkeypatch.setattr(win, "_hardware_mode_enabled", lambda: True)
    monkeypatch.setattr(main_window_module.sys, "platform", "win32")
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (f"C:/Users/me/{_TITLE_NAME}", "PNG (*.png)"),
    )
    path, ext = win._pick_export_path_for_figure(
        "Export Figure", _TITLE_NAME, main_window_module.FIGURE_EXPORT_FILTERS, default_filter="PNG (*.png)"
    )
    assert (path, ext) == (f"C:/Users/me/{_TITLE_NAME}.png", "png")
    win.close()


def test_export_figure_with_a_title_name_writes_a_png(tmp_path: Path, monkeypatch):
    _app()
    win = _loaded_window()
    out = tmp_path / _TITLE_NAME
    monkeypatch.setattr(win, "_pick_export_path_for_figure", lambda *args, **kwargs: (str(out), "png"))
    messages = []
    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: messages.append(("info", args[2])))
    monkeypatch.setattr(main_window_module.QMessageBox, "critical", lambda *args, **kwargs: messages.append(("error", args[2])))

    win.export_figure()

    saved = tmp_path / f"{_TITLE_NAME}.png"
    assert messages == [("info", f"Figure saved:\n{saved}")]
    assert not QImage(str(saved)).isNull()
    win.close()
