"""
e-CALLISTO FITS Analyzer
Offscreen tests for the Compare Viewpoint dialog (src/UI/multiview_dialog.py)
and its entry point in the Solar Image Analysis window.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtWidgets import QApplication

from src.Backend.sunpy_archive import SunPySearchRow
from src.UI.multiview_dialog import MultiViewpointDialog, build_spec_for_observable
from src.UI.solar_data_analysis_window import SolarDataAnalysisWindow


def _app():
    return QApplication.instance() or QApplication([])


class FakeFrame:
    observatory = "SDO"
    instrument = "AIA"
    detector = ""
    wavelength = "193 Angstrom"
    nickname = ""
    source = ""

    def __init__(self, data=None, *, date="2012-07-12T16:00:00"):
        self.data = np.asarray(data if data is not None else np.ones((8, 8)), dtype=float)
        self.date = date
        self.meta = {"instrume": "AIA"}


def _row(start: datetime) -> SunPySearchRow:
    return SunPySearchRow(
        start=start, end=start, source="SDO", instrument="AIA",
        provider="VSO", fileid=f"f{start:%H%M%S}", size="1 MB",
    )


def test_dialog_constructs_offscreen_without_network():
    _app()
    dialog = MultiViewpointDialog(
        None,
        reference_frames=[FakeFrame()],
        reference_label="AIA",
    )
    assert dialog.fetch_btn.isEnabled() is True
    assert dialog.blink_check.isEnabled() is False
    assert dialog.canvas_a.has_plot_content()
    dialog.close()


def test_choose_nearest_row_picks_closest_time():
    target = datetime(2012, 7, 12, 16, 0, 0)
    rows = [
        _row(datetime(2012, 7, 12, 16, 5, 0)),
        _row(datetime(2012, 7, 12, 15, 59, 0)),
        _row(datetime(2012, 7, 12, 17, 0, 0)),
    ]
    assert MultiViewpointDialog._choose_nearest_row(rows, target) == 1


def test_show_b_reprojected_renders_and_enables_blink():
    _app()
    dialog = MultiViewpointDialog(
        None,
        reference_frames=[FakeFrame()],
        reference_label="AIA",
    )
    fake_b = FakeFrame(np.full((8, 8), 5.0), date="2012-07-12T16:02:00")
    dialog._show_b(fake_b, reprojected=True, separation=87.5)

    assert dialog.canvas_b.has_plot_content()
    assert "87.5" in dialog.separation_label.text()
    assert dialog.blink_check.isEnabled() is True
    dialog.close()


def test_show_b_without_reprojection_keeps_blink_disabled():
    _app()
    dialog = MultiViewpointDialog(
        None,
        reference_frames=[FakeFrame()],
        reference_label="AIA",
    )
    dialog._show_b(FakeFrame(), reprojected=False, separation=None)
    assert dialog.blink_check.isEnabled() is False
    assert "n/a" in dialog.separation_label.text()
    dialog.close()


def test_blink_alternates_panel_a():
    _app()
    dialog = MultiViewpointDialog(
        None,
        reference_frames=[FakeFrame(np.zeros((8, 8)))],
        reference_label="AIA",
    )
    dialog._show_b(FakeFrame(np.full((8, 8), 9.0)), reprojected=True, separation=10.0)

    dialog._on_blink_tick()
    assert dialog._blink_showing_b is True
    first = np.array(dialog.canvas_a.map_image.image, copy=True)
    dialog._on_blink_tick()
    assert dialog._blink_showing_b is False
    second = np.array(dialog.canvas_a.map_image.image, copy=True)
    assert not np.array_equal(first, second)  # panels really alternate
    dialog.close()


def test_build_spec_for_observable_variants():
    t0 = datetime(2012, 7, 12, 15, 30)
    t1 = datetime(2012, 7, 12, 16, 30)

    aia = build_spec_for_observable("AIA", 193.0, t0, t1)
    assert (aia.spacecraft, aia.instrument, aia.wavelength_angstrom) == ("SDO", "AIA", 193.0)

    cor2 = build_spec_for_observable("SECCHI", ("STEREO_A", "COR2", None), t0, t1)
    assert (cor2.spacecraft, cor2.instrument, cor2.detector) == ("STEREO_A", "SECCHI", "COR2")
    assert cor2.wavelength_angstrom is None

    suvi = build_spec_for_observable("SUVI", 171.0, t0, t1)
    assert (suvi.spacecraft, suvi.instrument, suvi.level) == ("GOES", "SUVI", "1b")

    hmi = build_spec_for_observable("HMI", "magnetogram", t0, t1)
    assert (hmi.instrument, hmi.product) == ("HMI", "magnetogram")

    lasco = build_spec_for_observable("LASCO", "C3", t0, t1)
    assert (lasco.spacecraft, lasco.detector) == ("SOHO", "C3")


def test_window_compare_button_gating_and_original_frames(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    assert win.compare_viewpoint_btn.isEnabled() is False

    frames = [FakeFrame(np.ones((8, 8))), FakeFrame(np.ones((8, 8)), date="2012-07-12T16:10:00")]
    win._apply_loaded_frames(frames, paths=["a.fits", "b.fits"], metadata={})
    assert win.compare_viewpoint_btn.isEnabled() is True

    recorded = {}

    class _Recorder:
        def __init__(self, parent=None, **kwargs):
            recorded.update(kwargs)

        def show(self):
            recorded["shown"] = True

    import src.UI.multiview_dialog as mv

    monkeypatch.setattr(mv, "MultiViewpointDialog", _Recorder)
    win.open_multiview_dialog()

    assert recorded.get("shown") is True
    # The dialog gets the ORIGINAL loader outputs, not derived/cropped wrappers.
    assert recorded["reference_frames"] is win._original_frames
    assert recorded["reference_label"]
    win.close()
