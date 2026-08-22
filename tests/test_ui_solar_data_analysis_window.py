"""
e-CALLISTO FITS Analyzer
Version 2.8.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication

from src.Backend.jsoc_client import SIZE_BIN2, SIZE_CUTOUT, SIZE_FULL
from src.Backend.solar_data_analysis import AiaFrameSet, AiaMetadataRegion, frame_observation_time
from src.Backend.sunpy_archive import DATA_KIND_MAP, SunPyQuerySpec, SunPySearchResult, SunPySearchRow
from src.UI import solar_data_analysis_window as solar_mod
from src.UI.solar_data_analysis_window import SolarDataAnalysisWindow


def _app():
    return QApplication.instance() or QApplication([])


def _wait_for_worker(win, timeout_ms: int = 5000):
    """Block until a background worker thread finishes and its queued
    completion signals have been delivered on the main thread."""
    thread = getattr(win, "_active_thread", None)
    if thread is not None:
        thread.wait(timeout_ms)
    QApplication.processEvents()
    QApplication.processEvents()


class FakeMap:
    observatory = "SDO"
    instrument = "AIA"
    detector = ""
    wavelength = "193 Angstrom"
    date = "2026-02-10T01:00:00"
    nickname = ""
    source = ""

    def __init__(self, data):
        self.data = np.asarray(data, dtype=float)
        self.meta = {"instrume": "AIA"}


class FakeWcsMap(FakeMap):
    def __init__(self, data):
        super().__init__(data)
        self.meta = {
            "instrume": "AIA",
            "cdelt1": 2.0,
            "cdelt2": 2.0,
            "crpix1": 5.5,
            "crpix2": 5.5,
            "crval1": 0.0,
            "crval2": 0.0,
            "rsun_obs": 8.0,
        }


def test_solar_data_window_sidebar_keeps_plot_action_visible():
    _app()
    win = SolarDataAnalysisWindow()
    QApplication.processEvents()

    # The sidebar minimum adapts to the screen (>= 360 on small displays,
    # 520 on desktop monitors) and always matches the computed width.
    assert win.controls_scroll.minimumWidth() == win._sidebar_min_width
    assert win._sidebar_min_width >= 360
    assert win.controls_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    assert win.plot_mode_btn.isHidden() is False
    assert win.plot_mode_btn.isEnabled() is True
    assert win.plot_mode_btn.objectName() == "SolarPrimaryAction"
    win.close()


def test_solar_data_window_title_flags_experimental_beta():
    _app()
    win = SolarDataAnalysisWindow()
    assert win.windowTitle() == "Solar Image Analysis (Experimental) Beta v1.0"
    win.close()


def test_solar_data_window_about_dialog_points_to_github_issues():
    _app()
    win = SolarDataAnalysisWindow()

    menu_titles = [action.text() for action in win.menuBar().actions()]
    # About is its own top-level menu, positioned immediately right of Export.
    assert "About" in menu_titles
    assert menu_titles.index("About") == menu_titles.index("Export") + 1
    assert win.about_action.text() == "About Solar Image Analysis…"
    # NoRole keeps macOS from relocating an "About…" action into the app menu, so
    # it stays visible in this window's own menu bar.
    assert win.about_action.menuRole() == QAction.MenuRole.NoRole

    captured = {}

    def fake_exec(self):
        captured["text"] = self.text()
        captured["informative"] = self.informativeText()
        return None

    monkeypatched = pytest.MonkeyPatch()
    monkeypatched.setattr(solar_mod.QMessageBox, "exec", fake_exec, raising=False)
    try:
        win.about_action.trigger()
    finally:
        monkeypatched.undo()

    body = captured["text"] + captured["informative"]
    assert "Solar Image Analysis" in body
    assert "experimental beta" in body.lower()
    assert "github.com/SaanDev/e-Callisto_FITS_Analyzer/issues" in body
    win.close()


def test_solar_data_window_menu_bar_exposes_secondary_actions():
    _app()
    win = SolarDataAnalysisWindow()
    menu_titles = [action.text() for action in win.menuBar().actions()]

    assert "Data" in menu_titles
    assert "Analysis" in menu_titles
    assert "Movie" in menu_titles
    assert "Export" in menu_titles
    assert win.export_regions_action.isEnabled() is False

    data = np.zeros((20, 20), dtype=float)
    data[8:12, 8:12] = 50.0
    win._apply_loaded_frames([FakeMap(data)], paths=["a.fits"], metadata={})
    win.threshold_spin.setValue(90)
    win.min_area_spin.setValue(4)
    win.detect_active_regions()
    QApplication.processEvents()

    assert win.export_regions_action.isEnabled() is True
    win.close()


def test_solar_data_window_archive_results_are_readable():
    _app()
    start = datetime(2026, 2, 10, 1, 0, 0)
    rows = [
        SunPySearchRow(
            start=start + timedelta(minutes=i),
            end=start + timedelta(minutes=i + 1),
            source="SDO",
            instrument="AIA",
            provider="JSOC",
            fileid=f"aia.lev1_euv_12s[{i}]",
            size="12 MB",
            selected=True,
        )
        for i in range(3)
    ]
    result = SunPySearchResult(
        spec=SunPyQuerySpec(start, start + timedelta(minutes=3), "SDO", "AIA", 193.0),
        data_kind=DATA_KIND_MAP,
        rows=rows,
        raw_response=object(),
        row_index_map=[(0, i) for i in range(3)],
    )

    win = SolarDataAnalysisWindow()
    win._on_search_finished(result)
    QApplication.processEvents()

    assert win.archive_results_group.minimumHeight() >= 300
    assert win.results_table.minimumHeight() >= 225
    assert win.results_table.rowCount() == 3
    assert win.archive_results_status_label.text().startswith("3 record")
    assert win.results_table.item(0, 1).text() == "2026-02-10 01:00:00"
    assert win.results_table.item(0, 4).text() == "aia.lev1_euv_12s[0]"
    assert win._checked_rows() == [0, 1, 2]
    assert win.select_all_results_btn.isEnabled() is True
    assert win.deselect_all_results_btn.isEnabled() is True

    win.deselect_all_results()
    assert win._checked_rows() == []
    win.select_all_results()
    assert win._checked_rows() == [0, 1, 2]
    win.close()


def test_solar_data_window_high_resolution_is_opt_in():
    _app()
    win = SolarDataAnalysisWindow()

    spec = win._build_query_spec()
    assert spec.resolution is None
    assert win.high_resolution_check.isChecked() is False

    win.high_resolution_check.setChecked(True)
    spec = win._build_query_spec()
    assert spec.resolution == 1.0
    win.close()


def _select_observable(win, label: str) -> None:
    idx = win.wavelength_combo.findText(label)
    assert idx >= 0, f"observable {label!r} not found in the selector"
    win.wavelength_combo.setCurrentIndex(idx)


def test_solar_data_window_offers_stereo_and_suvi_observables():
    _app()
    win = SolarDataAnalysisWindow()
    labels = [win.wavelength_combo.itemText(i) for i in range(win.wavelength_combo.count())]
    for expected in ("STEREO-A/COR2", "STEREO-B/EUVI 195 A", "STEREO-A/HI1", "GOES/SUVI 171 A"):
        assert expected in labels
    win.close()


def test_solar_data_window_builds_stereo_cor2_query():
    _app()
    win = SolarDataAnalysisWindow()
    _select_observable(win, "STEREO-A/COR2")
    spec = win._build_query_spec()
    assert spec.spacecraft == "STEREO_A"
    assert spec.instrument == "SECCHI"
    assert spec.detector == "COR2"
    assert spec.wavelength_angstrom is None
    assert win._default_aia_colormap_name() == "stereocor2"
    win.close()


def test_solar_data_window_builds_stereo_euvi_query_with_wavelength():
    _app()
    win = SolarDataAnalysisWindow()
    _select_observable(win, "STEREO-B/EUVI 195 A")
    spec = win._build_query_spec()
    assert spec.spacecraft == "STEREO_B"
    assert spec.instrument == "SECCHI"
    assert spec.detector == "EUVI"
    assert spec.wavelength_angstrom == 195.0
    assert win._default_aia_colormap_name() == "euvi195"
    win.close()


def test_solar_data_window_builds_suvi_query():
    _app()
    win = SolarDataAnalysisWindow()
    _select_observable(win, "GOES/SUVI 171 A")
    spec = win._build_query_spec()
    assert spec.spacecraft == "GOES"
    assert spec.instrument == "SUVI"
    assert spec.wavelength_angstrom == 171.0
    # GOES-18 carries the operational SUVI (16 retired to storage in 2025).
    assert spec.satellite_number == 18
    assert spec.level == "1b"
    # The SUVI dataretriever client rejects queries carrying a Sample attr, so
    # the spec must not request a cadence.
    assert spec.sample_seconds is None
    assert win._default_aia_colormap_name() == "goes-rsuvi171"
    win.close()


def test_solar_data_window_gates_sdo_controls_for_stereo():
    _app()
    win = SolarDataAnalysisWindow()
    # STEREO/SUVI are VSO-only like LASCO: the JSOC/cutout/high-res controls
    # must be disabled so no JSOC fast-path is attempted for them.
    _select_observable(win, "STEREO-A/COR2")
    assert win.source_combo.isEnabled() is False
    assert win.frame_size_combo.isEnabled() is False
    assert win.high_resolution_check.isEnabled() is False
    win.close()


def test_solar_data_window_drops_odd_sized_frames_on_load():
    _app()
    win = SolarDataAnalysisWindow()
    # A STEREO/COR-like sequence with one odd-sized browse frame interspersed;
    # it must be excluded so running/base difference never mixes raw frames in.
    frames = [
        FakeMap(np.ones((8, 8))),
        FakeMap(np.ones((8, 8))),
        FakeMap(np.ones((4, 4))),
        FakeMap(np.ones((8, 8))),
    ]
    win._apply_loaded_frames(frames, paths=["a.fits"], metadata={})
    assert len(win._map_frames) == 3
    assert all(f.data.shape == (8, 8) for f in win._map_frames)
    assert "excluded 1 frame" in win.analysis_text.toPlainText()
    win.close()


class _CorFakeMap(FakeMap):
    """STEREO/COR2-like frame with a controllable polarizer state and exposure."""

    observatory = "STEREO_A"
    instrument = "SECCHI"
    detector = "COR2"
    wavelength = ""

    def __init__(self, data, *, polar=None, exptime=None, date="2012-07-12T16:00:00"):
        super().__init__(data)
        self.date = date
        self.meta = {"instrume": "SECCHI", "detector": "COR2"}
        if polar is not None:
            self.meta["polar"] = polar
        if exptime is not None:
            self.meta["exptime"] = exptime


def test_solar_data_window_excludes_polarizer_frames_on_load():
    _app()
    win = SolarDataAnalysisWindow()
    # The verified real-data case: total-brightness (POLAR=1001) frames mixed
    # with a same-size polarizer triplet (POLAR=0/120/240). Only the
    # total-brightness science sequence must survive.
    frames = [
        _CorFakeMap(np.ones((8, 8)), polar=1001.0, date="2012-07-12T15:39:00"),
        _CorFakeMap(np.ones((8, 8)), polar=0.0, date="2012-07-12T16:08:15"),
        _CorFakeMap(np.ones((8, 8)), polar=120.0, date="2012-07-12T16:08:45"),
        _CorFakeMap(np.ones((8, 8)), polar=240.0, date="2012-07-12T16:09:15"),
        _CorFakeMap(np.ones((8, 8)), polar=1001.0, date="2012-07-12T16:24:00"),
    ]
    win._apply_loaded_frames(frames, paths=["a.fts"], metadata={})
    assert len(win._map_frames) == 2
    assert all(f.meta.get("polar") == 1001.0 for f in win._map_frames)
    assert "excluded 3 frame(s)" in win.analysis_text.toPlainText()
    win.close()


def test_running_difference_normalizes_unequal_exposures():
    _app()
    win = SolarDataAnalysisWindow()
    # data/exptime chosen so the DN/s rate is identical: raw DN differencing
    # would show +100 of false signal; normalized differencing shows zero.
    frames = [
        _CorFakeMap(np.full((8, 8), 100.0), polar=1001.0, exptime=1.0,
                    date="2012-07-12T16:00:00"),
        _CorFakeMap(np.full((8, 8), 200.0), polar=1001.0, exptime=2.0,
                    date="2012-07-12T16:15:00"),
    ]
    win._apply_loaded_frames(frames, paths=["a.fts", "b.fts"], metadata={})
    assert win._exposure_varies is True

    win.movie_content_combo.setCurrentText("Running Difference")
    win.frame_slider.setValue(1)
    win._render_current_frame()
    assert win._current_map_data is not None
    assert np.allclose(win._current_map_data, 0.0)
    assert "DN/s" in win.plot_title_label.text()
    win.close()


def test_running_difference_keeps_raw_dn_for_equal_exposures():
    _app()
    win = SolarDataAnalysisWindow()
    frames = [
        _CorFakeMap(np.full((8, 8), 100.0), polar=1001.0, exptime=2.0,
                    date="2012-07-12T16:00:00"),
        _CorFakeMap(np.full((8, 8), 250.0), polar=1001.0, exptime=2.0,
                    date="2012-07-12T16:15:00"),
    ]
    win._apply_loaded_frames(frames, paths=["a.fts", "b.fts"], metadata={})
    assert win._exposure_varies is False

    win.movie_content_combo.setCurrentText("Running Difference")
    win.frame_slider.setValue(1)
    win._render_current_frame()
    assert np.allclose(win._current_map_data, 150.0)
    assert "DN/s" not in win.plot_title_label.text()
    assert "Running Difference" in win.plot_title_label.text()
    win.close()


def test_gating_disables_disk_tools_for_stereo_cor2():
    _app()
    win = SolarDataAnalysisWindow()
    frames = [
        _CorFakeMap(np.ones((8, 8)), polar=1001.0, date="2012-07-12T16:00:00"),
        _CorFakeMap(np.ones((8, 8)), polar=1001.0, date="2012-07-12T16:15:00"),
    ]
    win._apply_loaded_frames(frames, paths=["a.fts", "b.fts"], metadata={})
    # White-light coronagraph: no composites, magnetogram overlays or disk ARs.
    assert win.composite_btn.isEnabled() is False
    assert win.magnetogram_btn.isEnabled() is False
    assert win.detect_regions_btn.isEnabled() is False
    assert win.fetch_labels_btn.isEnabled() is False
    assert win.composite_action.isEnabled() is False
    assert win.detect_regions_action.isEnabled() is False
    assert win.labels_action.isEnabled() is False
    win.close()


class _EuviFakeMap(FakeMap):
    observatory = "STEREO_A"
    instrument = "SECCHI"
    detector = "EUVI"
    wavelength = "195 Angstrom"

    def __init__(self, data, *, date="2012-07-12T16:00:00"):
        super().__init__(data)
        self.date = date
        self.meta = {"instrume": "SECCHI", "detector": "EUVI", "wavelnth": 195}


def test_gating_keeps_disk_tools_for_euvi():
    _app()
    win = SolarDataAnalysisWindow()
    frames = [
        _EuviFakeMap(np.ones((8, 8)), date="2012-07-12T16:00:00"),
        _EuviFakeMap(np.ones((8, 8)), date="2012-07-12T16:10:00"),
    ]
    win._apply_loaded_frames(frames, paths=["a.fts", "b.fts"], metadata={})
    # EUVI is a disk EUV imager: region detection and composites stay usable.
    assert win.detect_regions_btn.isEnabled() is True
    assert win.composite_btn.isEnabled() is True
    assert win.detect_regions_action.isEnabled() is True
    win.close()


def test_lightcurve_requires_two_frames():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeMap(np.ones((8, 8)))], paths=["a.fits"], metadata={})
    assert win.lightcurve_btn.isEnabled() is False

    frames = [FakeMap(np.ones((8, 8))), FakeMap(np.ones((8, 8)))]
    win._apply_loaded_frames(frames, paths=["a.fits", "b.fits"], metadata={})
    assert win.lightcurve_btn.isEnabled() is True
    win.close()


def test_export_basename_and_frames_word_for_cor2():
    _app()
    win = SolarDataAnalysisWindow()
    frames = [
        _CorFakeMap(np.ones((8, 8)), polar=1001.0, date="2012-07-12T16:00:00"),
        _CorFakeMap(np.ones((8, 8)), polar=1001.0, date="2012-07-12T16:15:00"),
    ]
    win._apply_loaded_frames(frames, paths=["a.fts", "b.fts"], metadata={})
    assert win._frames_word() == "STEREO-A COR2"
    assert win._export_basename("movie") == "stereo_a_cor2_movie"
    assert "AIA" not in win._loaded_frame_status_text("Loaded", win._map_frames)
    win.close()


def test_apply_instrument_visibility_matrix():
    _app()
    win = SolarDataAnalysisWindow()

    def _select(label):
        idx = win.wavelength_combo.findText(label)
        assert idx >= 0, label
        win.wavelength_combo.setCurrentIndex(idx)
        QApplication.processEvents()

    # AIA (disk EUV): disk tools visible, coronagraph/HI/vector groups hidden.
    _select("AIA 193 A")
    assert win.coronagraph_group.isHidden()
    assert win.hi_group.isHidden()
    assert win.vector_group.isHidden()
    assert not win.composite_btn.isHidden()
    assert not win.region_group.isHidden()

    # HMI (magnetograph): vector group appears.
    _select("HMI Magnetogram")
    assert not win.vector_group.isHidden()
    assert win.coronagraph_group.isHidden()

    # STEREO COR2 (coronagraph): coronagraph tools appear, disk tools vanish.
    _select("STEREO-A/COR2")
    assert not win.coronagraph_group.isHidden()
    assert win.hi_group.isHidden()
    assert win.vector_group.isHidden()
    assert win.composite_btn.isHidden()
    assert win.detect_regions_btn.isHidden()
    assert win.region_group.isHidden()

    # STEREO HI1 (heliospheric): J-map group appears.
    _select("STEREO-A/HI1")
    assert not win.hi_group.isHidden()
    assert win.coronagraph_group.isHidden()

    # LASCO: coronagraph tools + the Helioviewer live preview.
    _select("SOHO/LASCO C2")
    assert not win.coronagraph_group.isHidden()
    assert not win.live_preview_btn.isHidden()
    _select("AIA 193 A")
    assert win.live_preview_btn.isHidden()
    win.close()


def test_visibility_follows_loaded_frames_over_observable():
    _app()
    win = SolarDataAnalysisWindow()
    # Observable stays AIA, but COR2 frames are loaded: loaded data wins.
    frames = [
        _CorFakeMap(np.ones((8, 8)), polar=1001.0, date="2012-07-12T16:00:00"),
        _CorFakeMap(np.ones((8, 8)), polar=1001.0, date="2012-07-12T16:15:00"),
    ]
    win._apply_loaded_frames(frames, paths=["a.fts", "b.fts"], metadata={})
    QApplication.processEvents()
    assert not win.coronagraph_group.isHidden()
    assert win.composite_btn.isHidden()
    win.close()


def test_load_summary_label_text():
    _app()
    win = SolarDataAnalysisWindow()
    assert win.load_summary_label.text() == "No data loaded."
    frames = [
        _CorFakeMap(np.ones((8, 8)), polar=1001.0, date="2012-07-12T16:00:00"),
        _CorFakeMap(np.ones((8, 8)), polar=1001.0, date="2012-07-12T16:15:00"),
    ]
    win._apply_loaded_frames(frames, paths=["a.fts", "b.fts"], metadata={})
    text = win.load_summary_label.text()
    assert "STEREO-A COR2" in text
    assert "2 frame(s)" in text
    assert "total-brightness" in text
    win.close()


def test_canvas_pixel_arcsec_roundtrip_and_hover_readout():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(
        [FakeWcsMap(np.arange(100, dtype=float).reshape(10, 10))], paths=["a.fits"], metadata={}
    )
    # Roundtrip through the canvas transforms is exact.
    x_arc, y_arc = win.pyqt_canvas.map_arcsec_from_pixel(3.0, 7.0)
    x_pix, y_pix = win.pyqt_canvas.map_pixel_from_arcsec(x_arc, y_arc)
    assert (x_pix, y_pix) == pytest.approx((3.0, 7.0))

    # Hover over disk centre: solar measures (R☉ + position angle) + pixels.
    win._on_canvas_hover(0.0, 0.0)
    text = win.coord_readout_label.text()
    assert "R☉" in text and "PA" in text and "px" in text
    assert "r = 0.00" in text  # disk centre is zero solar radii out

    # Leaving the image clears the readout.
    win._on_canvas_hover(None, None)
    assert win.coord_readout_label.text() == ""
    # Far outside the image also clears it.
    win._on_canvas_hover(1e6, 1e6)
    assert win.coord_readout_label.text() == ""
    win.close()


def _real_disk_map():
    """A real Earth-view full-disk helioprojective sunpy Map (offline)."""
    astropy_units = pytest.importorskip("astropy.units")
    pytest.importorskip("sunpy.map")
    from astropy.coordinates import SkyCoord
    import sunpy.map
    from sunpy.coordinates import get_earth
    from sunpy.map.header_helper import make_fitswcs_header

    obstime = "2020-01-01T00:00:00"
    data = np.zeros((64, 64))
    ref = SkyCoord(
        0 * astropy_units.arcsec,
        0 * astropy_units.arcsec,
        obstime=obstime,
        observer=get_earth(obstime),
        frame="helioprojective",
    )
    header = make_fitswcs_header(data, ref, scale=[40, 40] * astropy_units.arcsec / astropy_units.pix)
    return sunpy.map.Map(data, header)


def test_solar_coordinate_graticule_and_hci_hover():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([_real_disk_map()], paths=["disk.fits"], metadata={})
    win._render_current_frame()

    # Grid is on by default -> the curvilinear graticule is drawn on the canvas.
    assert win.grid_check.isChecked()
    assert win.pyqt_canvas.has_solar_graticule()

    # Hovering the disk centre adds the HCI longitude/latitude to the readout.
    win._on_canvas_hover(0.0, 0.0)
    text = win.coord_readout_label.text()
    assert "HCI lon=" in text and "lat=" in text
    assert "R☉" in text and "PA" in text and "px" in text

    # Switching the frame relabels the readout (Stonyhurst -> HGS).
    win.grid_frame_combo.setCurrentText(solar_mod.SOLAR_FRAME_DISPLAY_NAMES["HGS"])
    win._hover_lonlat_key = None  # bypass the per-pixel cache for the assertion
    win._on_canvas_hover(0.0, 0.0)
    assert "HGS lon=" in win.coord_readout_label.text()

    # The graticule follows a renderer switch onto the Matplotlib canvas.
    win.renderer_combo.setCurrentText("Matplotlib")
    QApplication.processEvents()
    assert win.matplotlib_canvas.has_solar_graticule()

    # Turning the grid off hides the graticule on the active canvas.
    win.grid_check.setChecked(False)
    assert not win.matplotlib_canvas.has_solar_graticule()
    win.close()


def test_sidebar_master_collapse_and_restore():
    _app()
    win = SolarDataAnalysisWindow()
    win.show()
    QApplication.processEvents()
    assert getattr(win, "_sidebar_collapsed", False) is False

    win._set_sidebar_collapsed(True, animate=False)
    assert win.controls_scroll.maximumWidth() == 0
    # The splitter pane is pinned to zero, so the collapse is guaranteed
    # visually and not just via the max-width constraint.
    assert win.main_splitter.sizes()[0] == 0

    win._set_sidebar_collapsed(False, animate=False)
    assert win.controls_scroll.maximumWidth() == win._sidebar_max_width
    assert win.controls_scroll.minimumWidth() == win._sidebar_min_width
    assert win.main_splitter.sizes()[0] > 0
    win.close()


def test_sidebar_group_accordion_collapses_and_regates():
    _app()
    win = SolarDataAnalysisWindow()
    assert win.mode_group.isCheckable()

    win.mode_group.setChecked(False)  # collapse Analysis Modes
    assert win.plot_mode_btn.isHidden()
    win.mode_group.setChecked(True)  # expand again
    assert not win.plot_mode_btn.isHidden()
    # Expanding a checkable group re-enables children wholesale; the re-gate
    # must restore the unloaded state (nothing loaded -> tools disabled).
    assert not win.difference_mode_btn.isEnabled()
    assert not win.lightcurve_btn.isEnabled()
    win.close()


def test_details_panel_toggle():
    _app()
    win = SolarDataAnalysisWindow()
    win.show()
    QApplication.processEvents()
    assert not win.analysis_text.isHidden()
    win.details_toggle_btn.setChecked(False)
    assert win.analysis_text.isHidden()
    win.details_toggle_btn.setChecked(True)
    assert not win.analysis_text.isHidden()
    win.close()


def test_solar_data_window_warns_before_large_high_resolution_download(monkeypatch):
    _app()
    start = datetime(2026, 2, 10, 1, 0, 0)
    rows = [
        SunPySearchRow(
            start=start + timedelta(minutes=i),
            end=start + timedelta(minutes=i + 1),
            source="SDO",
            instrument="AIA",
            provider="JSOC",
            fileid=f"aia.lev1_euv_12s[{i}]",
            size="12 MB",
            selected=True,
        )
        for i in range(10)
    ]
    result = SunPySearchResult(
        spec=SunPyQuerySpec(start, start + timedelta(minutes=10), "SDO", "AIA", 193.0, resolution=1.0),
        data_kind=DATA_KIND_MAP,
        rows=rows,
        raw_response=object(),
        row_index_map=[(0, i) for i in range(10)],
    )

    win = SolarDataAnalysisWindow()
    win._on_search_finished(result)
    win.high_resolution_check.setChecked(True)

    monkeypatch.setattr(solar_mod.QMessageBox, "question", staticmethod(lambda *_a, **_k: solar_mod.QMessageBox.No))
    started = []
    monkeypatch.setattr(win, "_start_worker", lambda worker: started.append(worker))
    win.download_and_load_selected()
    assert started == []

    monkeypatch.setattr(solar_mod.QMessageBox, "question", staticmethod(lambda *_a, **_k: solar_mod.QMessageBox.Yes))
    win.download_and_load_selected()
    assert started and started[-1].mode == "fetch_load"
    win.close()


def test_solar_data_window_progress_moves_smoothly():
    _app()
    win = SolarDataAnalysisWindow()

    win._set_busy(True, "Test")
    win._on_worker_progress(None, "Searching...")
    assert win.progress.maximum() == 0

    win._on_worker_progress(60, "Fetched")
    QApplication.processEvents()
    assert win.progress.maximum() == 100
    assert win._progress_target == 60
    assert win._progress_timer.isActive() is True
    win.close()


def test_solar_data_window_download_passes_jsoc_params(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()

    # Pretend a search produced one selectable SDO/AIA row.
    rows = [
        SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 0, 0),
            end=datetime(2026, 2, 10, 1, 1, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid="a.fits",
            size="1 MB",
        )
    ]
    win._search_result = SunPySearchResult(
        spec=SunPyQuerySpec(
            start_dt=datetime(2026, 2, 10, 1, 0, 0),
            end_dt=datetime(2026, 2, 10, 2, 0, 0),
            spacecraft="SDO",
            instrument="AIA",
            wavelength_angstrom=193.0,
        ),
        data_kind=DATA_KIND_MAP,
        rows=rows,
        raw_response=[[{"fileid": "a.fits"}]],
        row_index_map=[(0, 0)],
    )
    monkeypatch.setattr(win, "_checked_rows", lambda: [0])

    captured = {}

    def fake_start_worker(worker):
        captured["worker"] = worker

    monkeypatch.setattr(win, "_start_worker", fake_start_worker)

    win.source_combo.setCurrentIndex(win.source_combo.findData("jsoc"))
    win.jsoc_email_edit.setText("sci@example.org")
    win.download_and_load_selected()

    worker = captured.get("worker")
    assert worker is not None
    assert worker.jsoc_email == "sci@example.org"
    assert worker.prefer_jsoc is True
    win.close()


def _aia_search_result():
    rows = [
        SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 0, 0),
            end=datetime(2026, 2, 10, 1, 1, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid="a.fits",
            size="1 MB",
        )
    ]
    return SunPySearchResult(
        spec=SunPyQuerySpec(
            start_dt=datetime(2026, 2, 10, 1, 0, 0),
            end_dt=datetime(2026, 2, 10, 2, 0, 0),
            spacecraft="SDO",
            instrument="AIA",
            wavelength_angstrom=193.0,
        ),
        data_kind=DATA_KIND_MAP,
        rows=rows,
        raw_response=[[{"fileid": "a.fits"}]],
        row_index_map=[(0, 0)],
    )


def test_solar_data_window_cutout_builds_jsoc_process(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    win._search_result = _aia_search_result()
    monkeypatch.setattr(win, "_checked_rows", lambda: [0])
    captured = {}
    monkeypatch.setattr(win, "_start_worker", lambda w: captured.__setitem__("worker", w))

    win.jsoc_email_edit.setText("sci@example.org")
    win.frame_size_combo.setCurrentIndex(win.frame_size_combo.findData(SIZE_CUTOUT))
    win.cutout_x_spin.setValue(100.0)
    win.cutout_y_spin.setValue(-50.0)
    win.cutout_w_spin.setValue(400.0)
    win.cutout_h_spin.setValue(300.0)
    win.download_and_load_selected()

    worker = captured.get("worker")
    assert worker is not None
    assert worker.prefer_jsoc is True
    assert worker.jsoc_process and "im_patch" in worker.jsoc_process
    patch = worker.jsoc_process["im_patch"]
    assert patch["x"] == 100.0 and patch["width"] == 400.0
    win.close()


def test_solar_data_window_binned_requires_email(monkeypatch):
    _app()
    from PySide6.QtWidgets import QMessageBox

    win = SolarDataAnalysisWindow()
    win._search_result = _aia_search_result()
    monkeypatch.setattr(win, "_checked_rows", lambda: [0])
    started = []
    monkeypatch.setattr(win, "_start_worker", lambda w: started.append(w))
    info = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: info.append(a))

    win.jsoc_email_edit.setText("")  # no email
    win.frame_size_combo.setCurrentIndex(win.frame_size_combo.findData(SIZE_BIN2))
    win.download_and_load_selected()

    assert not started      # blocked
    assert info             # user was told to register / pick full disk
    win.close()


def test_solar_data_window_size_estimate_updates(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    monkeypatch.setattr(win, "_checked_rows", lambda: [0, 1, 2])

    win.frame_size_combo.setCurrentIndex(win.frame_size_combo.findData(SIZE_FULL))
    win._update_size_estimate()
    full_text = win.size_estimate_label.text()
    assert "3 frame" in full_text and "MB" in full_text

    win.frame_size_combo.setCurrentIndex(win.frame_size_combo.findData(SIZE_BIN2))
    win._update_size_estimate()
    assert "JSOC only" in win.size_estimate_label.text()
    win.close()


def _timed_frame(value, *, exptime=2.0, date="2026-02-10T01:00:00"):
    frame = FakeMap(np.full((6, 6), float(value)))
    frame.meta = {"instrume": "AIA", "exptime": exptime}
    frame.date = date
    return frame


def test_solar_data_window_region_lightcurve_dialog(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    win._map_frames = [
        _timed_frame(10.0, date="2026-02-10T01:00:00"),
        _timed_frame(40.0, date="2026-02-10T01:02:00"),
    ]
    win.crop_check.setChecked(False)
    shown = {}
    monkeypatch.setattr(solar_mod.RegionLightcurveDialog, "show", lambda self: shown.setdefault("ok", True))
    win.show_region_lightcurve()
    assert getattr(win, "_lightcurve_dialog", None) is not None
    assert shown.get("ok") is True
    win.close()


def test_solar_data_window_lightcurve_requires_sequence(monkeypatch):
    _app()
    from PySide6.QtWidgets import QMessageBox

    win = SolarDataAnalysisWindow()
    win._map_frames = [_timed_frame(10.0)]  # single frame -> needs a sequence
    info = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: info.append(a))
    monkeypatch.setattr(solar_mod.RegionLightcurveDialog, "show", lambda self: None)
    win.show_region_lightcurve()
    assert info and getattr(win, "_lightcurve_dialog", None) is None
    win.close()


def test_solar_data_window_radio_reference_window(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()

    class FakeParent:
        def _current_time_window_utc(self):
            return (datetime(2026, 2, 10, 1, 0, 0), datetime(2026, 2, 10, 1, 5, 0))

    monkeypatch.setattr(win, "parent", lambda: FakeParent())
    assert win._radio_reference_window() == (
        datetime(2026, 2, 10, 1, 0, 0),
        datetime(2026, 2, 10, 1, 5, 0),
    )
    win.close()


def test_region_lightcurve_dialog_renders_with_radio_overlay():
    from src.Backend.solar_data_analysis import AiaLightcurve

    _app()
    lc = AiaLightcurve(
        times=[datetime(2026, 2, 10, 1, 0, 0), datetime(2026, 2, 10, 1, 2, 0)],
        values=np.array([5.0, 20.0]),
        bounds=None,
        unit="DN/s",
        statistic="mean",
        wavelength="193 Angstrom",
    )
    dlg = solar_mod.RegionLightcurveDialog(
        lc, radio_window=(datetime(2026, 2, 10, 0, 59, 0), datetime(2026, 2, 10, 1, 1, 0))
    )
    assert dlg.canvas is not None
    dlg.close()


def test_solar_data_window_jsoc_settings_round_trip():
    _app()
    win = SolarDataAnalysisWindow()
    win.jsoc_email_edit.setText("persist@example.org")
    win.source_combo.setCurrentIndex(win.source_combo.findData("vso"))
    win._save_jsoc_settings()
    win.close()

    win2 = SolarDataAnalysisWindow()  # fresh instance restores from settings
    assert win2.jsoc_email_edit.text() == "persist@example.org"
    assert str(win2.source_combo.currentData()) == "vso"
    win2.close()


def test_solar_data_window_close_during_download_cancels_and_defers():
    from PySide6.QtGui import QCloseEvent

    _app()
    win = SolarDataAnalysisWindow()
    worker = solar_mod.SunPyWorker("fetch_load")

    class _FakeRunningThread:
        def isRunning(self):
            return True

    win._active_thread = _FakeRunningThread()
    win._active_worker = worker

    event = QCloseEvent()
    win.closeEvent(event)

    # The download is cancelled and the close is deferred (not destroyed).
    assert worker._cancel_event.is_set() is True
    assert win._pending_close is True
    assert event.isAccepted() is False

    # When the worker thread actually stops, the deferred close completes.
    win._active_thread = None
    win._on_worker_stopped()
    assert win._pending_close is False


def test_solar_data_window_byte_progress_drives_bar_and_defers_ticks():
    from src.Backend.download_manager import AggregateProgress

    _app()
    win = SolarDataAnalysisWindow()
    win._set_busy(True, "Downloading")

    agg = AggregateProgress(
        files_total=2,
        files_done=1,
        bytes_done=50,
        bytes_total=100,
        speed_bps=10,
        eta_seconds=5.0,
    )
    win._on_byte_progress(agg)
    QApplication.processEvents()
    # 1 of 2 files done -> progress_fraction 0.5 maps into the 5..85 band -> 45.
    assert win._byte_active is True
    assert win.progress.value() == 45
    assert win.progress_panel.stats_label.text() != ""

    # A coarse file-count tick inside the download band must NOT overwrite the
    # honest byte bar.
    win._on_worker_progress(70, "Downloading batch 2/4...")
    QApplication.processEvents()
    assert win.progress.value() == 45

    # Crossing into the loading phase (>85) releases byte mode.
    win._on_worker_progress(96, "Finalizing data...")
    QApplication.processEvents()
    assert win._byte_active is False
    win.close()


def test_solar_data_window_progress_pulses_during_long_download(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    clock = {"now": 10.0}
    monkeypatch.setattr(solar_mod.time, "monotonic", lambda: clock["now"])

    win._set_busy(True, "Downloading")
    win._on_worker_progress(5, "Downloading high-resolution batch 1/2...")
    win._progress_value = 5
    win.progress.setValue(5)
    clock["now"] = 11.0
    win._tick_progress()

    assert win.progress.value() == 6
    assert win._progress_activity is True
    win._set_busy(False)
    win.close()


def test_solar_data_window_progress_enters_busy_mode_at_soft_cap(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    clock = {"now": 10.0}
    monkeypatch.setattr(solar_mod.time, "monotonic", lambda: clock["now"])

    win._set_busy(True, "Downloading")
    win._on_worker_progress(5, "Downloading high-resolution batch 1/2...")
    win._progress_value = 86
    win._progress_target = 5
    win._progress_soft_cap = 86
    win.progress.setValue(86)
    clock["now"] = 11.0
    win._tick_progress()

    assert win.progress.maximum() == 0
    win._set_busy(False)
    win.close()


def test_solar_data_stop_active_operation_calls_worker_cancel(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()

    class FakeWorker:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    fake_worker = FakeWorker()
    win._active_worker = fake_worker  # type: ignore[assignment]
    monkeypatch.setattr(win, "is_operation_running", lambda: True)
    win.stop_btn.setEnabled(True)
    win.stop_action.setEnabled(True)

    win.stop_active_operation()

    assert fake_worker.cancelled is True
    assert win.stop_btn.isEnabled() is False
    assert win.stop_action.isEnabled() is False
    monkeypatch.setattr(win, "is_operation_running", lambda: False)
    win.close()


def test_solar_data_window_loads_local_fake_maps(monkeypatch):
    _app()

    progress = []

    def fake_load(paths, *, progress_cb=None, cancel_cb=None):
        if progress_cb is not None:
            for i in range(len(paths)):
                progress_cb(i + 1, len(paths))
        return AiaFrameSet(
            paths=list(paths),
            maps=[FakeMap(np.ones((8, 8))), FakeMap(np.full((8, 8), 3.0))],
            metadata={"n_frames": 2, "instrument": "AIA"},
        )

    monkeypatch.setattr(solar_mod, "load_aia_maps_streaming", fake_load)
    win = SolarDataAnalysisWindow()
    win.load_local_paths(["a.fits", "b.fits"])
    _wait_for_worker(win)

    assert len(win._map_frames) == 2
    assert not hasattr(win, "_plot_window")
    assert win.canvas.map_image.image is not None
    assert win.play_btn.isEnabled() is True
    assert win.export_movie_btn.isEnabled() is True

    win.show()
    win.resize(1500, 900)
    QApplication.processEvents()
    view_w, view_h = win.canvas.map_viewbox_size()
    assert abs(view_w - view_h) <= 1
    assert win.canvas.map_background_lightness() > 180
    assert win.canvas.map_low_color_lightness() < 80
    assert win.canvas.has_visible_colorbar() is True
    assert win.canvas.map_axis_labels() == ("Solar X (arcsec)", "Solar Y (arcsec)")
    assert win.canvas.map_plot.getAxis("bottom").autoSIPrefix is False
    assert win.canvas.map_plot.getAxis("left").autoSIPrefix is False
    assert win.colormap_combo.currentText() == "sdoaia193"
    assert win._resolved_colormap_name() == "sdoaia193"
    assert "8 x 8 px" in win.analysis_text.toPlainText()

    win.colorbar_check.setChecked(False)
    QApplication.processEvents()
    assert win.canvas.has_visible_colorbar() is False
    win.colorbar_check.setChecked(True)
    QApplication.processEvents()
    assert win.canvas.has_visible_colorbar() is True

    win.colormap_combo.setCurrentText("sdoaia193")
    QApplication.processEvents()
    assert win.canvas.colormap_name() == "sdoaia193"
    win.close()


def test_solar_data_window_matplotlib_renderer_is_light_and_square(monkeypatch):
    _app()

    def fake_load(paths, *, progress_cb=None, cancel_cb=None):
        data = np.zeros((12, 12), dtype=float)
        data[4:8, 4:8] = 50.0
        return AiaFrameSet(paths=list(paths), maps=[FakeMap(data)], metadata={"n_frames": 1, "instrument": "AIA"})

    monkeypatch.setattr(solar_mod, "load_aia_maps_streaming", fake_load)
    win = SolarDataAnalysisWindow()
    win.show()
    win.resize(1500, 900)
    win.renderer_combo.setCurrentText("Matplotlib")
    win.load_local_paths(["a.fits"])
    _wait_for_worker(win)

    canvas = win._active_canvas()
    assert canvas.backend_name() == "matplotlib"
    assert canvas.has_plot_content() is True
    assert canvas.map_background_lightness() > 180
    assert canvas.map_low_color_lightness() < 80
    assert win.colormap_combo.currentText() == "sdoaia193"
    assert canvas.colormap_name() == "sdoaia193"
    assert canvas.has_visible_colorbar() is True
    view_w, view_h = canvas.map_viewbox_size()
    assert abs(view_w - view_h) <= 2
    assert canvas.map_axis_labels() == ("Solar X (arcsec)", "Solar Y (arcsec)")
    win.close()


def test_solar_data_window_defaults_colormap_to_loaded_aia_wavelength():
    _app()
    frame = FakeMap(np.ones((6, 6), dtype=float))
    frame.wavelength = "171 Angstrom"

    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([frame], paths=["a.fits"], metadata={"instrument": "AIA"})
    QApplication.processEvents()

    assert win.colormap_combo.currentText() == "sdoaia171"
    assert win.canvas.colormap_name() == "sdoaia171"
    win.wavelength_combo.setCurrentText("AIA 193 A")
    QApplication.processEvents()
    assert win.colormap_combo.currentText() == "sdoaia171"
    win.close()


def test_solar_data_window_detects_regions_and_draws_overlays():
    _app()
    data = np.zeros((30, 30), dtype=float)
    data[6:12, 7:13] = 100.0
    data[20:25, 19:24] = 200.0

    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeMap(data)], paths=["a.fits"], metadata={"instrument": "AIA"})
    QApplication.processEvents()

    win.threshold_spin.setValue(95)
    win.min_area_spin.setValue(8)
    win.detect_active_regions()
    QApplication.processEvents()

    assert win.region_table.rowCount() == 2
    assert not hasattr(win, "_plot_window")
    assert win.canvas.region_overlay_count() >= 2
    win.close()


def test_solar_data_metadata_labels_existing_regions():
    _app()
    data = np.zeros((20, 20), dtype=float)
    data[8:12, 8:12] = 50.0

    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeMap(data)], paths=["a.fits"], metadata={})
    QApplication.processEvents()
    win.threshold_spin.setValue(90)
    win.min_area_spin.setValue(4)
    win.detect_active_regions()
    assert win._regions

    region = win._regions[0]
    win._on_metadata_finished(
        [
            AiaMetadataRegion(
                label="NOAA 12345",
                noaa_number="12345",
                center_x_arcsec=region.centroid_x_arcsec,
                center_y_arcsec=region.centroid_y_arcsec,
                source="HEK",
            )
        ]
    )
    QApplication.processEvents()

    assert win.region_table.item(0, 1).text() == "NOAA 12345"
    assert win.region_table.item(0, 2).text() == "12345"
    win.close()


def test_solar_data_window_applies_axis_coordinate_crop():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeMap(np.arange(100, dtype=float).reshape(10, 10))], paths=["a.fits"], metadata={})
    QApplication.processEvents()

    win.crop_check.setChecked(True)
    win.crop_x0_spin.setValue(-2.0)
    win.crop_x1_spin.setValue(2.0)
    win.crop_y0_spin.setValue(-2.0)
    win.crop_y1_spin.setValue(2.0)
    win.apply_axis_crop()
    QApplication.processEvents()

    assert win._map_frames[0].data.shape == (5, 5)
    assert win.canvas.map_image.image is not None
    win.close()


def test_solar_data_window_crops_from_coordinates_without_checkbox():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeMap(np.arange(100, dtype=float).reshape(10, 10))], paths=["a.fits"], metadata={})
    QApplication.processEvents()

    # The "Rectangle crop" checkbox is OFF; typing bounds + Apply must still work.
    assert win.crop_check.isChecked() is False
    win.crop_x0_spin.setValue(-2.0)
    win.crop_x1_spin.setValue(2.0)
    win.crop_y0_spin.setValue(-2.0)
    win.crop_y1_spin.setValue(2.0)
    win.apply_axis_crop()
    QApplication.processEvents()

    assert win._map_frames[0].data.shape == (5, 5)
    win.close()


def test_solar_data_window_apply_crop_without_frames_informs(monkeypatch):
    _app()
    from PySide6.QtWidgets import QMessageBox

    win = SolarDataAnalysisWindow()
    info = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: info.append(a))
    win.apply_axis_crop()  # no frames loaded
    assert info  # informed, no crash, no "enable checkbox" message
    win.close()


def test_solar_data_window_clip_sliders_drive_live_render(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeMap(np.arange(100, dtype=float).reshape(10, 10))], paths=["a.fits"], metadata={})
    QApplication.processEvents()

    # Slider values are exposed as float percent, like the old spin boxes.
    assert win.clip_low_slider.value() == 1.0
    assert win.clip_high_slider.value() == 99.9
    win.clip_low_slider.setValue(5.0)
    assert win.clip_low_slider.value() == 5.0
    assert "5.0%" in win.clip_low_slider.readout.text()

    # Dragging (moving the underlying QSlider) schedules a throttled live render.
    renders = {"n": 0}
    monkeypatch.setattr(win, "_render_current_frame", lambda: renders.__setitem__("n", renders["n"] + 1))
    win.clip_high_slider.slider.setValue(950)  # 95.0% drag -> valueChanged -> _schedule_clip_render
    QApplication.processEvents()
    assert renders["n"] >= 1  # rendered immediately on the leading edge
    assert win.clip_high_slider.value() == 95.0
    win.close()


def test_solar_data_window_uses_fits_wcs_for_crop_coordinates():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeWcsMap(np.arange(100, dtype=float).reshape(10, 10))], paths=["a.fits"], metadata={})
    QApplication.processEvents()

    assert win._current_axis_transform["x_scale_arcsec_per_pix"] == 2.0
    assert win._current_axis_transform["y_scale_arcsec_per_pix"] == 2.0
    assert win.canvas.map_view_rect()[2] == 20.0

    win.crop_check.setChecked(True)
    win.crop_x0_spin.setValue(-4.0)
    win.crop_x1_spin.setValue(4.0)
    win.crop_y0_spin.setValue(-4.0)
    win.crop_y1_spin.setValue(4.0)
    bounds = win._crop_bounds_from_axis_fields((10, 10))

    assert bounds == (2, 7, 2, 7)
    win.apply_axis_crop()
    QApplication.processEvents()
    assert win._map_frames[0].data.shape == (5, 5)
    assert win._current_axis_transform["x_ref_pix"] == 2.5
    assert win._current_axis_transform["y_ref_pix"] == 2.5
    assert win.canvas.map_view_rect()[2] == 10.0
    win.close()


def test_solar_data_window_save_selected_to_disk(monkeypatch, tmp_path):
    _app()
    from PySide6.QtWidgets import QFileDialog

    win = SolarDataAnalysisWindow()
    win._search_result = _aia_search_result()
    monkeypatch.setattr(win, "_checked_rows", lambda: [0])
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    captured = {}
    monkeypatch.setattr(win, "_start_worker", lambda w: captured.__setitem__("worker", w))

    win.save_selected_to_disk()

    worker = captured.get("worker")
    assert worker is not None
    assert str(worker.cache_dir) == str(tmp_path)   # downloads into the chosen folder
    assert win._save_target_dir == str(tmp_path)
    win.close()


def test_solar_data_window_reset_all_clears_state_and_cache(monkeypatch, tmp_path):
    _app()
    from PySide6.QtWidgets import QMessageBox

    win = SolarDataAnalysisWindow()
    win.cache_dir = tmp_path
    (tmp_path / "cached_frame.fits").write_bytes(b"x" * 16)

    # Dirty the state and a few controls.
    win._apply_loaded_frames([FakeMap(np.ones((8, 8)))], paths=["a.fits"], metadata={})
    win._search_result = _aia_search_result()
    win.wavelength_combo.setCurrentText("AIA 304 A")
    win.clip_low_slider.slider.setValue(120)   # 12.0%
    win.max_records_spin.setValue(500)
    QApplication.processEvents()

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    win.reset_all()
    QApplication.processEvents()

    # State cleared.
    assert win._map_frames == [] and win._search_result is None
    assert win.results_table.rowCount() == 0
    # Controls back to defaults.
    assert win.wavelength_combo.currentText() == "AIA 193 A"
    assert win.clip_low_slider.value() == 1.0
    assert win.clip_high_slider.value() == 99.9
    assert win.max_records_spin.value() == 120
    # Cache deleted.
    assert not (tmp_path / "cached_frame.fits").exists()
    win.close()


def test_solar_data_window_reset_all_declined_keeps_state(monkeypatch, tmp_path):
    _app()
    from PySide6.QtWidgets import QMessageBox

    win = SolarDataAnalysisWindow()
    win.cache_dir = tmp_path
    (tmp_path / "keep.fits").write_bytes(b"x" * 8)
    win._apply_loaded_frames([FakeMap(np.ones((8, 8)))], paths=["a.fits"], metadata={})
    QApplication.processEvents()

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    win.reset_all()

    assert win._map_frames != []                 # nothing cleared
    assert (tmp_path / "keep.fits").exists()      # cache untouched
    win.close()


def test_solar_data_window_query_spec_hmi_observable():
    _app()
    win = SolarDataAnalysisWindow()
    idx = win.wavelength_combo.findText("HMI Magnetogram")
    assert idx >= 0
    win.wavelength_combo.setCurrentIndex(idx)
    spec = win._build_query_spec()
    assert spec.instrument == "HMI"
    assert spec.product == "magnetogram"
    assert spec.wavelength_angstrom is None
    # HMI default colormap is the bipolar magnetogram map.
    assert win._default_aia_colormap_name() == "hmimag"
    win.close()


def test_solar_data_window_query_spec_aia_observable():
    _app()
    win = SolarDataAnalysisWindow()
    win.wavelength_combo.setCurrentText("AIA 304 A")
    spec = win._build_query_spec()
    assert spec.instrument == "AIA"
    assert spec.wavelength_angstrom == 304.0
    assert spec.product is None
    win.close()


def _lasco_frame(data=None, detector="C2"):
    frame = FakeMap(np.ones((6, 6), dtype=float) if data is None else data)
    frame.observatory = "SOHO"
    frame.instrument = "LASCO"
    frame.detector = detector
    frame.wavelength = None
    frame.meta = {"instrume": "LASCO"}
    return frame


def test_solar_data_window_query_spec_lasco_observable():
    _app()
    win = SolarDataAnalysisWindow()
    for detector, cmap in (("C2", "soholasco2"), ("C3", "soholasco3")):
        idx = win.wavelength_combo.findText(f"SOHO/LASCO {detector}")
        assert idx >= 0
        win.wavelength_combo.setCurrentIndex(idx)
        spec = win._build_query_spec()
        assert spec.spacecraft == "SOHO"
        assert spec.instrument == "LASCO"
        assert spec.detector == detector
        # Coronagraph: no EUV wavelength, no JSOC resolution, no HMI product.
        assert spec.wavelength_angstrom is None
        assert spec.resolution is None
        assert spec.product is None
        assert win._default_aia_colormap_name() == cmap
    win.close()


def test_solar_data_window_lasco_disables_sdo_download_controls():
    _app()
    win = SolarDataAnalysisWindow()
    idx = win.wavelength_combo.findText("SOHO/LASCO C2")
    win.wavelength_combo.setCurrentIndex(idx)
    # LASCO is VSO-only, full-disk only: JSOC / frame-size / high-res are off.
    assert not win.source_combo.isEnabled()
    assert not win.jsoc_email_edit.isEnabled()
    assert not win.frame_size_combo.isEnabled()
    assert not win.high_resolution_check.isEnabled()
    assert win.frame_size_combo.currentData() == SIZE_FULL
    # Switching back to an SDO/AIA observable restores them.
    win.wavelength_combo.setCurrentText("AIA 193 A")
    assert win.source_combo.isEnabled()
    assert win.frame_size_combo.isEnabled()
    assert win.high_resolution_check.isEnabled()
    win.close()


def test_solar_data_window_lasco_gates_euv_only_tools():
    _app()
    win = SolarDataAnalysisWindow()
    # Two frames so the sequence tools (light curve needs >= 2) stay enabled.
    win._apply_loaded_frames(
        [_lasco_frame(detector="C2"), _lasco_frame(detector="C2")],
        paths=["c2_a.fts", "c2_b.fts"],
        metadata={},
    )
    QApplication.processEvents()

    assert win._loaded_is_lasco() is True
    assert win._loaded_instrument_label() == "LASCO C2"
    assert "C2" in win._frame_title(win._map_frames[0], 0)
    assert win.colormap_combo.currentText() == "soholasco2"

    # EUV/disk-only tools are disabled for a coronagraph sequence...
    assert not win.composite_btn.isEnabled()
    assert not win.magnetogram_btn.isEnabled()
    assert not win.detect_regions_btn.isEnabled()
    assert not win.composite_action.isEnabled()
    assert not win.detect_regions_action.isEnabled()
    # ...while the mission-agnostic tools stay available.
    assert win.difference_mode_btn.isEnabled()
    assert win.crop_check.isEnabled()
    assert win.export_movie_btn.isEnabled()
    assert win.lightcurve_btn.isEnabled()

    # Loading an SDO/AIA sequence re-enables the EUV tools.
    win._apply_loaded_frames([FakeMap(np.ones((6, 6)))], paths=["a.fits"], metadata={"instrument": "AIA"})
    QApplication.processEvents()
    assert win._loaded_is_lasco() is False
    assert win.composite_btn.isEnabled()
    assert win.detect_regions_btn.isEnabled()
    win.close()


def test_solar_data_window_composite_uses_magnetogram_overlay(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeMap(np.random.rand(16, 16) * 100.0)], paths=["a.fits"], metadata={})
    QApplication.processEvents()

    mag = np.zeros((16, 16), dtype=float)
    mag[2:6, 2:6] = 400.0
    mag[10:14, 10:14] = -400.0
    win._overlay_magnetogram = FakeMap(mag)
    win.show_composite_plot()
    QApplication.processEvents()

    # Composite frame is RGB with polarity contours overlaid.
    frame = win._map_frames[0]
    assert frame.data.ndim == 3 and frame.data.shape[-1] == 3
    assert "magnetogram" in win.analysis_text.toPlainText().lower()
    win.close()


def test_solar_data_window_sorts_uploaded_frames_by_time(monkeypatch):
    _app()

    def _frame(value, date):
        f = FakeMap(np.full((6, 6), float(value)))
        f.date = date
        return f

    # Provided out of chronological order (values tag the intended time order).
    shuffled = [
        _frame(3, "2026-02-10T01:04:00"),
        _frame(1, "2026-02-10T01:00:00"),
        _frame(4, "2026-02-10T01:06:00"),
        _frame(2, "2026-02-10T01:02:00"),
    ]

    def fake_load(paths, *, progress_cb=None, cancel_cb=None):
        return AiaFrameSet(paths=list(paths), maps=shuffled, metadata={"n_frames": 4, "instrument": "AIA"})

    monkeypatch.setattr(solar_mod, "load_aia_maps_streaming", fake_load)
    win = SolarDataAnalysisWindow()
    win.load_local_paths(["c.fits", "a.fits", "d.fits", "b.fits"])
    _wait_for_worker(win)

    values = [float(f.data[0, 0]) for f in win._map_frames]
    assert values == [1.0, 2.0, 3.0, 4.0]   # chronological, not upload order
    assert [float(f.data[0, 0]) for f in win._original_frames] == [1.0, 2.0, 3.0, 4.0]
    win.close()


def test_solar_data_window_sort_keeps_untimed_frames_last():
    _app()
    win = SolarDataAnalysisWindow()

    def _frame(value, date=None):
        f = FakeMap(np.full((4, 4), float(value)))
        f.date = date  # None -> no observation time
        return f

    frames = [_frame(9, None), _frame(2, "2026-02-10T01:02:00"), _frame(1, "2026-02-10T01:00:00")]
    ordered = win._sort_frames_by_time(frames)
    vals = [float(f.data[0, 0]) for f in ordered]
    assert vals == [1.0, 2.0, 9.0]   # timed sorted first, untimed kept at the end
    win.close()


def test_solar_data_window_export_movie_runs_in_background(monkeypatch, tmp_path):
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(
        [FakeMap(np.ones((8, 8))), FakeMap(np.full((8, 8), 2.0))], paths=["a.fits"], metadata={}
    )
    QApplication.processEvents()

    out = str(tmp_path / "m.mp4")
    monkeypatch.setattr(solar_mod, "pick_export_path", lambda *a, **k: (out, ""))
    monkeypatch.setattr(solar_mod, "_imageio_ffmpeg_available", lambda: True)
    captured = {}
    monkeypatch.setattr(win, "_start_worker", lambda w: captured.__setitem__("worker", w))

    win.scale_combo.setCurrentText("log")
    win.export_movie()

    worker = captured.get("worker")
    assert isinstance(worker, solar_mod.MovieExportWorker)   # not blocking on the UI thread
    assert worker._spec.path == out
    assert worker._spec.scale == "log"
    assert worker._spec.percentile_low == win.clip_low_slider.value()
    win.close()


def test_solar_data_window_export_movie_offers_gif_without_ffmpeg(monkeypatch, tmp_path):
    _app()
    from PySide6.QtWidgets import QMessageBox

    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeMap(np.ones((8, 8)))], paths=["a.fits"], metadata={})
    QApplication.processEvents()

    monkeypatch.setattr(solar_mod, "pick_export_path", lambda *a, **k: (str(tmp_path / "m.mp4"), ""))
    monkeypatch.setattr(solar_mod, "_imageio_ffmpeg_available", lambda: False)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    captured = {}
    monkeypatch.setattr(win, "_start_worker", lambda w: captured.__setitem__("worker", w))

    win.export_movie()

    worker = captured.get("worker")
    assert worker is not None
    assert worker._spec.path.endswith(".gif")   # fell back to GIF
    win.close()


def test_solar_data_window_export_progress_updates_bar():
    _app()
    win = SolarDataAnalysisWindow()
    win._set_busy(True, "Exporting")
    win._on_export_progress(3, 12)
    assert win.progress.value() == 25                       # 3/12
    assert "3 of 12" in win.progress_panel.stats_label.text()
    win.close()


def test_solar_data_window_cropped_image_fills_view():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeWcsMap(np.arange(100, dtype=float).reshape(10, 10))], paths=["a.fits"], metadata={})
    QApplication.processEvents()

    win.crop_x0_spin.setValue(-4.0)
    win.crop_x1_spin.setValue(4.0)
    win.crop_y0_spin.setValue(-4.0)
    win.crop_y1_spin.setValue(4.0)
    win.apply_axis_crop()
    QApplication.processEvents()

    # Regression: the cropped image used to be scaled by the *previous* frame's
    # pixel size (setRect before setImage) and shrink into the bottom-left
    # corner. The image extent must now match the zoomed view exactly.
    img = win.pyqt_canvas.map_image
    irect = img.mapRectToView(img.boundingRect()).getRect()
    vrect = win.pyqt_canvas.map_view_rect()
    assert abs(irect[2] - vrect[2]) < 1e-6  # width fills the view
    assert abs(irect[3] - vrect[3]) < 1e-6  # height fills the view
    win.close()


def test_solar_data_window_crops_from_interactive_rectangle():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeWcsMap(np.arange(100, dtype=float).reshape(10, 10))], paths=["a.fits"], metadata={})
    QApplication.processEvents()

    win.renderer_combo.setCurrentText("Matplotlib")
    QApplication.processEvents()
    assert win._active_canvas().backend_name() == "matplotlib"

    win.crop_check.setChecked(True)
    QApplication.processEvents()
    assert win._active_canvas().backend_name() == "pyqtgraph"
    assert win.pyqt_canvas.roi_selector_active() is True

    win.pyqt_canvas.set_roi_arcsec_bounds(-4.0, 4.0, -4.0, 4.0)
    QApplication.processEvents()
    roi_bounds = win.pyqt_canvas._roi_bounds

    assert roi_bounds == (3, 7, 3, 7)
    assert win._crop_bounds_from_axis_fields((10, 10)) == roi_bounds

    win.apply_axis_crop()
    QApplication.processEvents()
    assert win._map_frames[0].data.shape == (4, 4)
    assert win.crop_check.isChecked() is False
    assert win.pyqt_canvas.roi_selector_active() is False
    win.close()


def test_solar_data_window_find_latest_starts_find_latest_worker(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    idx = win.wavelength_combo.findText("SOHO/LASCO C2")
    win.wavelength_combo.setCurrentIndex(idx)

    captured = {}
    monkeypatch.setattr(win, "_start_worker", lambda worker: captured.setdefault("worker", worker))
    win.find_latest_data()

    assert win._pending_latest is True
    assert captured["worker"].mode == "find_latest"
    spec = captured["worker"].query_spec
    assert (spec.spacecraft, spec.instrument, spec.detector) == ("SOHO", "LASCO", "C2")
    win.close()


def test_solar_data_window_find_latest_announces_and_syncs_window():
    _app()
    win = SolarDataAnalysisWindow()
    idx = win.wavelength_combo.findText("SOHO/LASCO C2")
    win.wavelength_combo.setCurrentIndex(idx)

    # Synthetic "latest available" result, roughly 16 months behind real time.
    t = datetime(2025, 2, 16, 18, 24)
    rows = [
        SunPySearchRow(
            start=t - timedelta(minutes=12 * i), end=t, source="SOHO",
            instrument="LASCO", provider="SDAC", fileid=f"f{i}.fts", size="2 MB",
        )
        for i in range(4)
    ]
    spec = SunPyQuerySpec(
        start_dt=t - timedelta(hours=6), end_dt=t + timedelta(minutes=1),
        spacecraft="SOHO", instrument="LASCO", detector="C2",
    )
    result = SunPySearchResult(
        spec=spec, data_kind=DATA_KIND_MAP, rows=rows,
        raw_response=[rows], row_index_map=[(0, i) for i in range(len(rows))],
    )

    win._pending_latest = True
    win._on_search_finished(result)

    assert win._pending_latest is False
    assert win.results_table.rowCount() == 4
    # Query window fields jump to the newest available data.
    assert win.end_dt_edit.dateTime().toString("yyyy-MM-dd") == "2025-02-16"
    text = win.analysis_text.toPlainText()
    assert "Latest available SOHO/LASCO C2" in text
    assert "behind real time" in text
    assert "CME Catalog" in text  # near-real-time movie pointer for LASCO
    win.close()


def test_solar_data_window_live_preview_enabled_only_for_lasco():
    _app()
    win = SolarDataAnalysisWindow()
    win.wavelength_combo.setCurrentText("AIA 193 A")
    assert not win.live_preview_btn.isEnabled()
    assert not win.live_preview_action.isEnabled()

    idx = win.wavelength_combo.findText("SOHO/LASCO C2")
    win.wavelength_combo.setCurrentIndex(idx)
    assert win.live_preview_btn.isEnabled()
    assert win.live_preview_action.isEnabled()
    win.close()


def test_solar_data_window_opens_helioviewer_preview(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    idx = win.wavelength_combo.findText("SOHO/LASCO C3")
    win.wavelength_combo.setCurrentIndex(idx)

    import src.UI.helioviewer_preview_dialog as hvd

    def _boom(detector, **kw):  # avoid any real network call from the dialog worker
        raise RuntimeError("stubbed")

    monkeypatch.setattr(hvd, "fetch_preview", _boom)
    win.open_helioviewer_preview()
    dialog = win._helioviewer_dialog
    assert dialog is not None
    assert dialog._current_detector() == "C3"
    # Let the (stubbed) worker finish before teardown.
    for _ in range(100):
        QApplication.processEvents()
        if not dialog._is_loading():
            break
    dialog.close()
    win.close()


# -- HMI vector magnetic field overlay ---------------------------------------


class FakeHmiMap(FakeMap):
    instrument = "HMI"
    wavelength = ""

    def __init__(self, data):
        super().__init__(data)
        self.meta = {"instrume": "HMI_SIDE1", "content": "MAGNETOGRAM"}


def _make_vector_frame(when=None, shape=(32, 32)):
    from src.Backend.hmi_vector_field import HmiVectorFrame

    ny, nx = shape
    transform = {
        "x_ref_pix": (nx - 1) / 2.0,
        "y_ref_pix": (ny - 1) / 2.0,
        "x_scale_arcsec_per_pix": 1.0,
        "y_scale_arcsec_per_pix": 1.0,
        "x_ref_arcsec": 0.0,
        "y_ref_arcsec": 0.0,
    }
    return HmiVectorFrame(
        time=when or datetime(2026, 2, 10, 1, 0, 0),
        bx=np.full(shape, 300.0, dtype=np.float32),
        by=np.full(shape, 400.0, dtype=np.float32),
        bz=np.full(shape, 250.0, dtype=np.float32),
        axis_transform=transform,
        meta={},
    )


def test_solar_data_window_vector_field_controls_exist_with_defaults():
    _app()
    win = SolarDataAnalysisWindow()

    assert win.vector_load_btn.isEnabled()
    assert win.vector_download_btn.isEnabled()
    assert win.vector_show_check.isChecked() is False
    assert win.vector_arrows_check.isChecked() is True
    assert win.vector_stream_check.isChecked() is False
    assert win.vector_mag_check.isChecked() is False
    assert win.vector_spacing_spin.value() == 64
    assert win.vector_threshold_spin.value() == 200
    assert "no vector field" in win.vector_status_label.text().lower()
    win.close()


def test_solar_data_window_vector_overlay_draws_on_hmi_frames():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(
        [FakeHmiMap(np.random.rand(32, 32) * 100.0)], paths=["m.fits"], metadata={"instrument": "HMI"}
    )
    QApplication.processEvents()

    win._vector_frames = [_make_vector_frame()]
    win.vector_spacing_spin.setValue(16)
    win.vector_threshold_spin.setValue(100)
    win.vector_show_check.setChecked(True)
    QApplication.processEvents()

    assert win.pyqt_canvas.has_vector_field_overlay() is True
    assert "arrow" in win.vector_status_label.text().lower()

    win.vector_show_check.setChecked(False)
    QApplication.processEvents()
    assert win.pyqt_canvas.has_vector_field_overlay() is False
    win.close()


def test_solar_data_window_vector_overlay_skips_non_hmi_frames():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeMap(np.ones((16, 16)))], paths=["a.fits"], metadata={"instrument": "AIA"})
    QApplication.processEvents()

    win._vector_frames = [_make_vector_frame()]
    win.vector_show_check.setChecked(True)
    QApplication.processEvents()

    assert win.pyqt_canvas.has_vector_field_overlay() is False
    assert "hmi" in win.vector_status_label.text().lower()
    win.close()


def test_solar_data_window_vector_overlay_renders_on_matplotlib_canvas():
    _app()
    win = SolarDataAnalysisWindow()
    win.renderer_combo.setCurrentText("Matplotlib")
    win._apply_loaded_frames(
        [FakeHmiMap(np.random.rand(32, 32) * 100.0)], paths=["m.fits"], metadata={"instrument": "HMI"}
    )
    QApplication.processEvents()

    win._vector_frames = [_make_vector_frame()]
    win.vector_spacing_spin.setValue(16)
    win.vector_threshold_spin.setValue(100)
    win.vector_mag_check.setChecked(True)
    win.vector_show_check.setChecked(True)
    QApplication.processEvents()

    assert win.matplotlib_canvas.has_vector_field_overlay() is True
    win.close()


def test_solar_data_window_vector_frames_loaded_updates_state():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(
        [FakeHmiMap(np.random.rand(32, 32) * 100.0)], paths=["m.fits"], metadata={"instrument": "HMI"}
    )
    QApplication.processEvents()

    frames = [_make_vector_frame(), _make_vector_frame(when=datetime(2026, 2, 10, 1, 12, 0))]
    win._on_vector_frames_loaded(frames)
    QApplication.processEvents()

    assert win._vector_frames == frames
    assert win.vector_show_check.isChecked() is True
    assert "2 vector time step" in win.vector_status_label.text() or "arrow" in win.vector_status_label.text().lower()
    assert "hmi.b_720s" in win.analysis_text.toPlainText().lower()
    win.close()


def test_solar_data_window_vector_download_requires_jsoc_email(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    win.jsoc_email_edit.setText("")

    seen = {}

    def _info(parent, title, text, *args, **kwargs):
        seen["text"] = text
        return solar_mod.QMessageBox.Ok

    monkeypatch.setattr(solar_mod.QMessageBox, "information", _info)
    win.download_vector_field()
    assert "e-mail" in seen.get("text", "").lower()
    assert win.is_operation_running() is False
    win.close()


def test_solar_data_window_vector_download_plots_bz_when_nothing_loaded():
    _app()
    win = SolarDataAnalysisWindow()
    assert not win._map_frames

    win._on_vector_frames_loaded([_make_vector_frame()])
    QApplication.processEvents()

    # The vector data itself is plotted (as its Bz magnetogram) so the
    # download is immediately visible, with the overlay on top.
    assert len(win._map_frames) == 1
    assert win._frame_hmi_product(win._map_frames[0]) == "magnetogram"
    assert win.pyqt_canvas.has_plot_content() is True
    assert win.pyqt_canvas.has_vector_field_overlay() is True
    assert win.vector_show_check.isChecked() is True
    assert "bz" in win.analysis_text.toPlainText().lower()
    win.close()


def test_solar_data_window_vector_download_keeps_loaded_hmi_frames():
    _app()
    win = SolarDataAnalysisWindow()
    hmi_frame = FakeHmiMap(np.random.rand(32, 32) * 100.0)
    win._apply_loaded_frames([hmi_frame], paths=["m.fits"], metadata={"instrument": "HMI"})
    QApplication.processEvents()

    win.vector_spacing_spin.setValue(16)
    win.vector_threshold_spin.setValue(100)
    win._on_vector_frames_loaded([_make_vector_frame()])
    QApplication.processEvents()

    # An already-loaded HMI sequence stays; the overlay lands on it.
    assert win._map_frames == [hmi_frame]
    assert win.pyqt_canvas.has_vector_field_overlay() is True
    win.close()


def test_solar_data_window_vector_download_asks_before_replacing_aia(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    aia_frame = FakeMap(np.ones((16, 16)))
    win._apply_loaded_frames([aia_frame], paths=["a.fits"], metadata={"instrument": "AIA"})
    QApplication.processEvents()

    answers = {"reply": solar_mod.QMessageBox.No}
    monkeypatch.setattr(
        solar_mod.QMessageBox, "question", lambda *a, **k: answers["reply"]
    )

    # Declined: loaded AIA frames stay, no overlay is drawn on them.
    win._on_vector_frames_loaded([_make_vector_frame()])
    QApplication.processEvents()
    assert win._map_frames == [aia_frame]
    assert win.pyqt_canvas.has_vector_field_overlay() is False

    # Accepted: the Bz magnetogram replaces the AIA frames and the overlay shows.
    answers["reply"] = solar_mod.QMessageBox.Yes
    win.vector_spacing_spin.setValue(16)
    win.vector_threshold_spin.setValue(100)
    win._on_vector_frames_loaded([_make_vector_frame()])
    QApplication.processEvents()
    assert win._frame_hmi_product(win._map_frames[0]) == "magnetogram"
    assert win.pyqt_canvas.has_vector_field_overlay() is True
    win.close()


def test_vector_download_worker_emits_no_records(monkeypatch, tmp_path):
    _app()
    import src.Backend.jsoc_client as jc

    def _raise_empty(**kwargs):
        raise jc.JsocEmptyRecordSetError("empty window", latest_trec="2026.06.18_12:00:00_TAI")

    monkeypatch.setattr(jc, "export_hmi_vector_urls", _raise_empty)
    worker = solar_mod.VectorFieldDownloadWorker(
        start_dt=datetime(2026, 7, 2, 8, 0, 0),
        end_dt=datetime(2026, 7, 2, 10, 0, 0),
        cadence_seconds=720,
        email="sci@example.org",
        cache_dir=tmp_path,
    )
    seen = {"no_records": [], "failed": []}
    worker.no_records.connect(seen["no_records"].append)
    worker.failed.connect(seen["failed"].append)
    worker.run()

    # The empty window is reported through the dedicated signal (with the
    # newest available record), never as a raw traceback failure.
    assert seen["no_records"] == ["2026.06.18_12:00:00_TAI"]
    assert seen["failed"] == []


def test_solar_data_window_vector_no_records_offers_newest(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    calls = {"download": 0}
    monkeypatch.setattr(win, "download_vector_field", lambda: calls.__setitem__("download", calls["download"] + 1))
    monkeypatch.setattr(solar_mod.QMessageBox, "question", lambda *a, **k: solar_mod.QMessageBox.Yes)

    win._on_vector_no_records("2026.06.18_12:00:00_TAI")

    # Accepting moves the query window to the newest record and re-downloads.
    assert calls["download"] == 1
    assert win.end_dt_edit.dateTime().toString("yyyy-MM-dd") == "2026-06-18"
    assert win.start_dt_edit.dateTime().toPython() < win.end_dt_edit.dateTime().toPython()
    win.close()


def test_solar_data_window_vector_no_records_decline_keeps_window(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    before_start = win.start_dt_edit.dateTime().toPython()
    before_end = win.end_dt_edit.dateTime().toPython()
    calls = {"download": 0}
    monkeypatch.setattr(win, "download_vector_field", lambda: calls.__setitem__("download", calls["download"] + 1))
    monkeypatch.setattr(solar_mod.QMessageBox, "question", lambda *a, **k: solar_mod.QMessageBox.No)

    win._on_vector_no_records("2026.06.18_12:00:00_TAI")

    assert calls["download"] == 0
    assert win.start_dt_edit.dateTime().toPython() == before_start
    assert win.end_dt_edit.dateTime().toPython() == before_end
    win.close()


def test_solar_data_window_vector_no_records_without_latest(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    seen = {}
    monkeypatch.setattr(
        solar_mod.QMessageBox, "information", lambda parent, title, text, *a, **k: seen.setdefault("text", text)
    )
    calls = {"download": 0}
    monkeypatch.setattr(win, "download_vector_field", lambda: calls.__setitem__("download", calls["download"] + 1))

    win._on_vector_no_records("")

    assert calls["download"] == 0
    assert "earlier time window" in seen.get("text", "").lower()
    win.close()


# --------------------------------------------------------------------------- #
# Overlay Layers: multi-instrument coronagraph composites
# --------------------------------------------------------------------------- #
class FakeCoronagraphMap(FakeMap):
    """A LASCO-like frame with enough WCS metadata to composite onto."""

    observatory = "SOHO"
    instrument = "LASCO"
    detector = "C2"
    wavelength = ""
    date = "2012-07-12T16:00:00"

    def __init__(self, data, detector="C2"):
        super().__init__(data)
        self.detector = detector
        self.meta = {
            "instrume": "LASCO",
            "detector": detector,
            "cdelt1": 11.9,
            "cdelt2": 11.9,
            "crpix1": 32.5,
            "crpix2": 32.5,
            "crval1": 0.0,
            "crval2": 0.0,
            "rsun_obs": 945.0,
        }


def _coronagraph_frames(count=3, detector="C2", size=64):
    yy, xx = np.mgrid[0:size, 0:size]
    radius = np.hypot(xx - size / 2, yy - size / 2) + 1.0
    return [FakeCoronagraphMap(1000.0 / radius, detector) for _ in range(count)]


def _rgb_composites(count=3, size=64):
    frames = []
    for _ in range(count):
        rgb = np.zeros((size, size, 3), np.uint8)
        rgb[..., 0] = 200
        rgb[..., 1] = 100
        frames.append(rgb)
    return frames


def test_overlay_group_visible_only_for_coronagraph_data():
    _app()
    win = SolarDataAnalysisWindow()

    win._apply_loaded_frames([FakeWcsMap(np.ones((64, 64)))], paths=[], metadata={})
    assert win.overlay_group.isHidden()

    win._apply_loaded_frames(_coronagraph_frames(), paths=[], metadata={})
    assert not win.overlay_group.isHidden()
    assert not win.coronagraph_group.isHidden()
    win.close()


def test_overlay_layer_add_edit_and_remove():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(_coronagraph_frames(), paths=[], metadata={})

    idx = win.overlay_add_combo.findText("AIA 193 A")
    win.overlay_add_combo.setCurrentIndex(idx)
    win.add_overlay_layer()

    assert len(win._overlay_specs) == 1
    assert win.overlay_list.count() == 1
    spec = win._overlay_specs[0]
    assert spec.instrument == "AIA"
    assert spec.colormap == "sdoaia193"
    # A disk imager fills the occulted centre, so it is masked to the disk.
    assert spec.resolved_fov() == (0.0, 1.28)
    assert spec.resolved_screen() == "surface"
    # Editors follow the selection.
    assert win.overlay_colormap_combo.isEnabled()
    assert win.overlay_colormap_combo.currentText() == "sdoaia193"

    win.overlay_opacity_slider.setValue(40)
    assert win._overlay_specs[0].opacity == pytest.approx(0.4)

    win.overlay_list.setCurrentRow(0)
    win.remove_overlay_layer()
    assert win._overlay_specs == []
    assert win.overlay_list.count() == 0
    win.close()


def test_overlay_coronagraph_layer_is_flagged_approximate():
    """Cross-observer white-light overlays use a screen assumption; say so."""
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(_coronagraph_frames(), paths=[], metadata={})

    idx = win.overlay_add_combo.findText("STEREO-A/COR2")
    win.overlay_add_combo.setCurrentIndex(idx)
    win.add_overlay_layer()

    spec = win._overlay_specs[0]
    assert spec.resolved_screen() == "spherical"
    assert spec.is_approximate()
    assert "~" in win.overlay_list.item(0).text()
    assert "approximate" in win.overlay_note_label.text().lower()
    win.close()


def test_overlay_layer_checkbox_toggles_enabled():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(_coronagraph_frames(), paths=[], metadata={})
    win.overlay_add_combo.setCurrentIndex(win.overlay_add_combo.findText("AIA 193 A"))
    win.add_overlay_layer()

    win.overlay_list.item(0).setCheckState(Qt.Unchecked)
    assert win._overlay_specs[0].enabled is False
    win.overlay_list.item(0).setCheckState(Qt.Checked)
    assert win._overlay_specs[0].enabled is True
    win.close()


def test_composite_renders_as_rgb_without_a_colorbar():
    """A built composite is painted as-is and leaves the measure data 2-D."""
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(_coronagraph_frames(), paths=[], metadata={})
    win.overlay_add_combo.setCurrentIndex(win.overlay_add_combo.findText("AIA 193 A"))
    win.add_overlay_layer()

    win._composite_frames = _rgb_composites()
    win._render_current_frame()

    canvas = win._active_canvas()
    painted = np.asarray(canvas.map_image.image)
    assert painted.dtype == np.uint8
    assert painted.ndim == 3 and painted.shape[-1] == 3
    # Explicit identity levels: stale greyscale levels would render the
    # composite almost black, and None crashes once autoDownsample floats it.
    assert list(canvas.map_image.levels) == [0, 255]
    assert not canvas.has_visible_colorbar()
    # region_stats and the measure tools need a 2-D array.
    assert win._current_map_data.ndim == 2
    # No build ran here (the cache was seeded directly), so the title falls back
    # to the generic wording rather than naming layers it cannot vouch for.
    assert "overlay composite" in win.plot_title_label.text()
    win.close()


def test_composite_survives_region_stats():
    _app()
    from src.Backend.image_measure import region_stats

    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(_coronagraph_frames(), paths=[], metadata={})
    win._composite_frames = _rgb_composites()
    win._render_current_frame()

    stats = region_stats(win._current_map_data, (10, 30, 10, 30))
    assert np.isfinite(stats.mean)
    win.close()


def test_editing_a_layer_invalidates_the_composite():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(_coronagraph_frames(), paths=[], metadata={})
    win.overlay_add_combo.setCurrentIndex(win.overlay_add_combo.findText("AIA 193 A"))
    win.add_overlay_layer()
    win._composite_frames = _rgb_composites()

    win.overlay_opacity_slider.setValue(50)
    assert win._composite_frames == []
    win.close()


def test_clear_composite_keeps_the_layer_stack():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(_coronagraph_frames(), paths=[], metadata={})
    win.overlay_add_combo.setCurrentIndex(win.overlay_add_combo.findText("AIA 193 A"))
    win.add_overlay_layer()
    win._composite_frames = _rgb_composites()

    win.clear_composite()
    assert win._composite_frames == []
    assert len(win._overlay_specs) == 1
    canvas = win._active_canvas()
    assert np.asarray(canvas.map_image.image).ndim == 2
    win.close()


def test_loading_a_new_series_drops_layers_and_composite():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(_coronagraph_frames(), paths=[], metadata={})
    win.overlay_add_combo.setCurrentIndex(win.overlay_add_combo.findText("AIA 193 A"))
    win.add_overlay_layer()
    win._composite_frames = _rgb_composites()

    win._apply_loaded_frames(_coronagraph_frames(detector="C3"), paths=[], metadata={})
    assert win._overlay_specs == []
    assert win._composite_frames == []
    assert win.overlay_list.count() == 0
    win.close()


def test_build_composite_refuses_frames_without_world_coordinates(monkeypatch):
    """Derived arrays cannot be reprojected onto; say why instead of crashing."""
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(_coronagraph_frames(), paths=[], metadata={})
    win.overlay_add_combo.setCurrentIndex(win.overlay_add_combo.findText("AIA 193 A"))
    win.add_overlay_layer()

    seen = {}
    monkeypatch.setattr(
        solar_mod.QMessageBox, "warning",
        lambda *a, **k: seen.update(text=a[2] if len(a) > 2 else ""),
    )
    started = []
    monkeypatch.setattr(win, "_start_worker", lambda w: started.append(w))

    win.build_composite()
    assert started == []
    assert "world coordinates" in seen.get("text", "").lower()
    win.close()


def test_build_composite_starts_a_worker(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    frames = _coronagraph_frames()
    for frame in frames:  # a real Map exposes reproject_to
        frame.reproject_to = lambda *a, **k: frame
    win._apply_loaded_frames(frames, paths=[], metadata={})
    win.overlay_add_combo.setCurrentIndex(win.overlay_add_combo.findText("AIA 193 A"))
    win.add_overlay_layer()

    started = []
    monkeypatch.setattr(win, "_start_worker", lambda w: started.append(w))
    win.build_composite()

    assert len(started) == 1
    assert isinstance(started[0], solar_mod.CompositeBuildWorker)
    assert win.overlay_progress.isVisibleTo(win.overlay_group)
    win.close()


def test_composite_export_uses_raw_rgb_frames(monkeypatch, tmp_path):
    """The exported movie must be the composite, not the plain base."""
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(_coronagraph_frames(), paths=[], metadata={})
    win._composite_frames = _rgb_composites()
    win._render_current_frame()

    out = tmp_path / "movie.gif"
    monkeypatch.setattr(solar_mod, "pick_export_path", lambda *a, **k: (str(out), ""))
    monkeypatch.setattr(win, "_set_difference_mode", lambda mode: None)
    win.movie_content_combo.setCurrentText("Running Difference")
    started = []
    monkeypatch.setattr(win, "_start_worker", lambda w: started.append(w))

    win.export_movie()
    assert len(started) == 1
    worker = started[0]
    # Composites already carry the per-layer stretch and difference, so they are
    # exported as raw RGB rather than differenced again.
    assert worker._spec.mode == "raw"
    assert np.asarray(worker._frames[0].data).shape[-1] == 3
    win.close()


def test_overlay_layers_round_trip_through_a_session():
    _app()
    import json

    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(_coronagraph_frames(), paths=[], metadata={})
    win.overlay_add_combo.setCurrentIndex(win.overlay_add_combo.findText("STEREO-A/COR2"))
    win.add_overlay_layer()
    win.overlay_opacity_slider.setValue(60)
    win.overlay_window_spin.setValue(45)

    meta = json.loads(json.dumps(win._collect_session_meta()))
    assert meta["overlay_layers"]["window_minutes"] == 45

    other = SolarDataAnalysisWindow()
    other._apply_loaded_frames(_coronagraph_frames(), paths=[], metadata={})
    other._restore_overlay_layers(meta["overlay_layers"])

    assert len(other._overlay_specs) == 1
    restored = other._overlay_specs[0]
    # JSON turns the SECCHI tuple into a list; it must come back as a tuple so
    # the layer hashes and compares equal to a freshly added one.
    assert restored == win._overlay_specs[0]
    assert isinstance(restored.value, tuple)
    assert restored.opacity == pytest.approx(0.6)
    assert other.overlay_window_spin.value() == 45
    win.close()
    other.close()


def test_restore_overlay_layers_ignores_malformed_entries():
    _app()
    win = SolarDataAnalysisWindow()
    win._restore_overlay_layers({"layers": [{"bogus": 1}, {"instrument": ""}, "not-a-dict", 7]})
    assert win._overlay_specs == []
    win._restore_overlay_layers(None)
    win._restore_overlay_layers("nonsense")
    assert win._overlay_specs == []
    win.close()


# --------------------------------------------------------------------------- #
# CompositeBuildWorker: the download -> reproject -> blend orchestration.
# The archive and reprojection are stubbed so this runs offline.
# --------------------------------------------------------------------------- #
def test_composite_worker_row_selection_is_bounded_by_base_frames():
    """One file per base frame, not the whole search result."""
    rows = [type("Row", (), {"start": datetime(2012, 7, 12, 16, 0) + timedelta(minutes=i)})()
            for i in range(120)]
    targets = [datetime(2012, 7, 12, 16, 0) + timedelta(minutes=30 * i) for i in range(3)]
    picked = solar_mod.CompositeBuildWorker._rows_for_times(rows, targets)
    assert picked == [0, 30, 60]


def test_composite_worker_row_selection_deduplicates():
    rows = [type("Row", (), {"start": datetime(2012, 7, 12, 16, 0)})()]
    targets = [datetime(2012, 7, 12, 16, 0) + timedelta(minutes=i) for i in range(4)]
    assert solar_mod.CompositeBuildWorker._rows_for_times(rows, targets) == [0]


def test_composite_worker_difference_uses_the_layers_own_cadence():
    arrays = {0: np.full((2, 2), 1.0), 2: np.full((2, 2), 5.0), 5: np.full((2, 2), 9.0)}
    running = solar_mod.CompositeBuildWorker._difference(arrays, [0, 2, 5], "running")
    # Index 2 differences against index 0 (its own predecessor), not a base frame.
    assert running[2].mean() == pytest.approx(4.0)
    assert running[5].mean() == pytest.approx(4.0)

    base = solar_mod.CompositeBuildWorker._difference(arrays, [0, 2, 5], "base")
    assert base[0].mean() == pytest.approx(0.0)
    assert base[5].mean() == pytest.approx(8.0)

    # Raw mode and single-frame series are passed straight through.
    assert solar_mod.CompositeBuildWorker._difference(arrays, [0, 2, 5], "raw") is arrays
    single = {0: arrays[0]}
    assert solar_mod.CompositeBuildWorker._difference(single, [0], "running") is single


def test_composite_worker_builds_frames_end_to_end(monkeypatch):
    """Full orchestration with the archive and reprojection stubbed out."""
    from src.Backend.coronagraph_composite import LayerSpec

    base_frames = _coronagraph_frames(count=3)
    for i, frame in enumerate(base_frames):
        frame.date = f"2012-07-12T16:{i * 20:02d}:00"

    layer_frames = _coronagraph_frames(count=2)
    for i, frame in enumerate(layer_frames):
        frame.date = f"2012-07-12T16:{i * 30:02d}:00"
        frame.instrument = "AIA"

    monkeypatch.setattr(
        solar_mod.CompositeBuildWorker, "_layer_sources",
        lambda self, spec, base_times: (
            [f"layer{i}.fits" for i in range(len(layer_frames))],
            [frame_observation_time(f) for f in layer_frames],
        ),
    )
    monkeypatch.setattr(
        solar_mod.CompositeBuildWorker, "_load_one_layer_frame",
        lambda self, path, spec: layer_frames[int(path[5:-5])],
    )
    # Reprojection is the identity here: the stub layer is already on the grid.
    monkeypatch.setattr(
        "src.Backend.multiview.reproject_map_to",
        lambda source, target, **kw: source,
    )

    worker = solar_mod.CompositeBuildWorker(
        base_frames,
        LayerSpec("LASCO", "C2", "C2", colormap="soholasco2", scale="linear"),
        [LayerSpec("AIA", 193.0, "AIA 193", colormap="sdoaia193", scale="linear")],
    )
    captured = {}
    worker.finished.connect(lambda frames, notes: captured.update(frames=frames, notes=notes))
    worker.run()

    assert "frames" in captured, captured
    assert len(captured["frames"]) == 3
    for rgb in captured["frames"]:
        assert rgb.dtype == np.uint8
        assert rgb.shape == (64, 64, 3)
    # Nothing was skipped or dropped. The observer note is expected: these fakes
    # carry no DSUN_OBS, exactly like real LASCO level-0.5 headers.
    assert not [n for n in captured["notes"] if "failed" in n or "no matching" in n]
    assert all("observer position" in n for n in captured["notes"])


def test_composite_worker_drops_a_failing_layer_with_a_warning(monkeypatch):
    """A layer with no archive data must not abort the whole build."""
    from src.Backend.coronagraph_composite import LayerSpec

    base_frames = _coronagraph_frames(count=2)

    def explode(self, spec, base_times):
        raise RuntimeError("No AIA 193 records in the window.")

    monkeypatch.setattr(solar_mod.CompositeBuildWorker, "_layer_sources", explode)

    worker = solar_mod.CompositeBuildWorker(
        base_frames,
        LayerSpec("LASCO", "C2", "C2", colormap="soholasco2", scale="linear"),
        [LayerSpec("AIA", 193.0, "AIA 193")],
    )
    captured = {}
    worker.finished.connect(lambda frames, notes: captured.update(frames=frames, notes=notes))
    worker.run()

    # The base still composites; the failure is reported, not raised.
    assert len(captured["frames"]) == 2
    assert any("No AIA 193 records" in note for note in captured["notes"])


def test_composite_worker_reports_reprojection_failure(monkeypatch):
    from src.Backend.coronagraph_composite import LayerSpec

    stub = _coronagraph_frames(count=1)
    monkeypatch.setattr(
        solar_mod.CompositeBuildWorker, "_layer_sources",
        lambda self, spec, base_times: (["layer0.fits"], [frame_observation_time(stub[0])]),
    )
    monkeypatch.setattr(
        solar_mod.CompositeBuildWorker, "_load_one_layer_frame",
        lambda self, path, spec: stub[0],
    )

    def bad_reproject(source, target, **kw):
        raise ValueError("no overlap")

    monkeypatch.setattr("src.Backend.multiview.reproject_map_to", bad_reproject)

    worker = solar_mod.CompositeBuildWorker(
        _coronagraph_frames(count=2),
        LayerSpec("LASCO", "C2", "C2", colormap="soholasco2", scale="linear"),
        [LayerSpec("AIA", 193.0, "AIA 193")],
    )
    captured = {}
    worker.finished.connect(lambda frames, notes: captured.update(frames=frames, notes=notes))
    worker.run()

    assert len(captured["frames"]) == 2
    assert any("reprojection failed" in note for note in captured["notes"])


def test_composite_worker_emits_cancelled(monkeypatch):
    from src.Backend.coronagraph_composite import LayerSpec

    worker = solar_mod.CompositeBuildWorker(
        _coronagraph_frames(count=2),
        LayerSpec("LASCO", "C2", "C2"),
        [LayerSpec("AIA", 193.0, "AIA 193")],
    )
    worker.cancel()
    seen = {}
    worker.cancelled.connect(lambda: seen.update(cancelled=True))
    worker.finished.connect(lambda *a: seen.update(finished=True))
    worker.run()
    assert seen == {"cancelled": True}


def test_composite_worker_requires_base_frames():
    from src.Backend.coronagraph_composite import LayerSpec

    worker = solar_mod.CompositeBuildWorker([], LayerSpec("LASCO", "C2", "C2"), [])
    seen = {}
    worker.failed.connect(lambda tb: seen.update(tb=tb))
    worker.run()
    assert "coronagraph series" in seen.get("tb", "")


def test_composite_worker_handles_mixed_image_geometries(monkeypatch):
    """LASCO days interleave full-resolution and on-board summed frames.

    1024x1024 at 11.9"/px next to 512x512 at 23.8"/px cannot share a
    reprojection target or a radial mask, so each grid is composited on its own.
    """
    from src.Backend.coronagraph_composite import LayerSpec

    big = _coronagraph_frames(count=2, size=64)
    small = _coronagraph_frames(count=2, size=32)
    for frame in small:
        frame.meta = dict(frame.meta, cdelt1=23.8, cdelt2=23.8, crpix1=16.5, crpix2=16.5)
    base_frames = [big[0], small[0], big[1], small[1]]
    for i, frame in enumerate(base_frames):
        frame.date = f"2020-08-16T09:{i * 10:02d}:00"

    layer_frames = _coronagraph_frames(count=1, size=64)
    layer_frames[0].date = "2020-08-16T09:05:00"

    monkeypatch.setattr(
        solar_mod.CompositeBuildWorker, "_layer_sources",
        lambda self, spec, base_times: (
            [f"layer{i}.fits" for i in range(len(layer_frames))],
            [frame_observation_time(f) for f in layer_frames],
        ),
    )
    monkeypatch.setattr(
        solar_mod.CompositeBuildWorker, "_load_one_layer_frame",
        lambda self, path, spec: layer_frames[int(path[5:-5])],
    )
    # Identity reprojection returns a frame sized for whichever base it targets.
    monkeypatch.setattr(
        "src.Backend.multiview.reproject_map_to",
        lambda source, target, **kw: _coronagraph_frames(
            count=1, size=np.asarray(target.data).shape[0])[0],
    )

    worker = solar_mod.CompositeBuildWorker(
        base_frames,
        LayerSpec("LASCO", "C3", "C3", colormap="soholasco3", scale="linear"),
        [LayerSpec("LASCO", "C2", "C2", colormap="soholasco2", scale="linear")],
        window_minutes=120,
    )
    captured = {}
    worker.finished.connect(lambda frames, notes: captured.update(frames=frames, notes=notes))
    worker.run()

    frames, notes = captured["frames"], captured["notes"]
    assert len(frames) == 4
    # Each composite matches the shape of the base frame it came from.
    assert [f.shape[:2] for f in frames] == [(64, 64), (32, 32), (64, 64), (32, 32)]
    assert all(f.dtype == np.uint8 for f in frames)
    assert any("mixes 2 image geometries" in note for note in notes)


def test_composite_worker_geometry_key_separates_binned_frames():
    big = _coronagraph_frames(count=1, size=64)[0]
    small = _coronagraph_frames(count=1, size=32)[0]
    small.meta = dict(small.meta, cdelt1=23.8, crpix1=16.5, crpix2=16.5)
    key = solar_mod.CompositeBuildWorker._geometry_key
    assert key(big) != key(small)
    assert key(big) == key(_coronagraph_frames(count=1, size=64)[0])


def test_composite_worker_reports_an_empty_time_window(monkeypatch):
    """Downloaded frames that all fall outside the window must be explained."""
    from src.Backend.coronagraph_composite import LayerSpec

    base_frames = _coronagraph_frames(count=2)
    for i, frame in enumerate(base_frames):
        frame.date = f"2020-08-16T09:{i * 10:02d}:00"
    layer_frames = _coronagraph_frames(count=2)
    for i, frame in enumerate(layer_frames):
        frame.date = f"2020-08-16T23:{i * 10:02d}:00"   # ~14 hours later

    monkeypatch.setattr(
        solar_mod.CompositeBuildWorker, "_layer_sources",
        lambda self, spec, base_times: (
            [f"layer{i}.fits" for i in range(len(layer_frames))],
            [frame_observation_time(f) for f in layer_frames],
        ),
    )
    monkeypatch.setattr(
        solar_mod.CompositeBuildWorker, "_load_one_layer_frame",
        lambda self, path, spec: layer_frames[int(path[5:-5])],
    )
    worker = solar_mod.CompositeBuildWorker(
        base_frames, LayerSpec("LASCO", "C3", "C3"),
        [LayerSpec("LASCO", "C2", "C2")], window_minutes=30,
    )
    captured = {}
    worker.finished.connect(lambda frames, notes: captured.update(frames=frames, notes=notes))
    worker.run()

    assert len(captured["frames"]) == 2
    assert any("none fell within" in note and "widen" in note.lower()
               for note in captured["notes"])


def test_row_selection_is_capped_and_spans_the_range():
    """Regression: an uncapped selection downloaded a file per base frame.

    A full-disk AIA frame is ~67 MB, so 100 base frames meant gigabytes and the
    process was killed by the OS.
    """
    start = datetime(2020, 8, 16, 0, 0)
    rows = [type("Row", (), {"start": start + timedelta(minutes=i)})() for i in range(600)]
    targets = [start + timedelta(minutes=2 * i) for i in range(300)]

    picked = solar_mod.CompositeBuildWorker._rows_for_times(rows, targets, limit=48)
    assert len(picked) == 48
    assert len(set(picked)) == 48
    # Thinned evenly, so the kept frames still cover the whole time range.
    assert min(picked) < 20 and max(picked) > 550


def test_row_selection_below_the_cap_is_untouched():
    start = datetime(2020, 8, 16, 0, 0)
    rows = [type("Row", (), {"start": start + timedelta(minutes=10 * i)})() for i in range(5)]
    targets = [start + timedelta(minutes=10 * i) for i in range(5)]
    assert solar_mod.CompositeBuildWorker._rows_for_times(rows, targets, limit=48) == [0, 1, 2, 3, 4]


def test_header_time_reads_lasco_split_timestamps(tmp_path):
    """LASCO puts the date in DATE-OBS and the time in TIME-OBS."""
    from astropy.io import fits

    path = tmp_path / "lasco.fts"
    hdu = fits.PrimaryHDU(np.zeros((4, 4), np.float32))
    hdu.header["DATE-OBS"] = "2020/08/16"
    hdu.header["TIME-OBS"] = "09:24:05.528"
    hdu.writeto(path)
    assert solar_mod.CompositeBuildWorker._header_time(str(path)) == datetime(
        2020, 8, 16, 9, 24, 5, 528000
    )


def test_header_time_reads_a_full_iso_timestamp(tmp_path):
    from astropy.io import fits

    path = tmp_path / "aia.fits"
    hdu = fits.PrimaryHDU(np.zeros((4, 4), np.float32))
    hdu.header["DATE-OBS"] = "2020-08-16T09:24:05"
    hdu.writeto(path)
    assert solar_mod.CompositeBuildWorker._header_time(str(path)) == datetime(2020, 8, 16, 9, 24, 5)


def test_header_time_returns_none_for_unreadable_files(tmp_path):
    bad = tmp_path / "not-fits.txt"
    bad.write_text("nope")
    assert solar_mod.CompositeBuildWorker._header_time(str(bad)) is None


def test_no_records_message_explains_a_pre_launch_date():
    """Asking for AIA over 1997 LASCO must say why, not just 'no records'."""
    from src.Backend.coronagraph_composite import LayerSpec

    worker = solar_mod.CompositeBuildWorker([], LayerSpec("LASCO", "C2", "C2"), [])
    message = worker._no_records_message(
        LayerSpec("AIA", 193.0, "AIA 193"),
        [datetime(1997, 11, 14, 3, 0), datetime(1997, 11, 14, 5, 0)],
    )
    assert "1997-11-14" in message
    assert "2010" in message
    assert "was operating" in message


def test_no_records_message_for_a_date_the_instrument_covers():
    from src.Backend.coronagraph_composite import LayerSpec

    worker = solar_mod.CompositeBuildWorker(
        [], LayerSpec("LASCO", "C2", "C2"), [], window_minutes=30
    )
    message = worker._no_records_message(
        LayerSpec("AIA", 193.0, "AIA 193"),
        [datetime(2020, 8, 16, 9, 0), datetime(2020, 8, 16, 11, 0)],
    )
    assert "30 min" in message
    assert "Widen" in message


def test_raw_mode_does_not_materialise_every_base_frame(monkeypatch):
    """Raw builds must read base pixels per frame, not hold the whole series."""
    from src.Backend.coronagraph_composite import LayerSpec

    reads = {"n": 0}

    class CountingFrame(FakeCoronagraphMap):
        @property
        def data(self):
            reads["n"] += 1
            return self._data

        @data.setter
        def data(self, value):
            self._data = np.asarray(value, dtype=float)

    frames = []
    for i in range(4):
        frame = CountingFrame(np.ones((32, 32)))
        frame.date = f"2020-08-16T09:{i * 10:02d}:00"
        frames.append(frame)

    worker = solar_mod.CompositeBuildWorker(
        frames, LayerSpec("LASCO", "C3", "C3", scale="linear"), [], difference_mode="raw"
    )
    captured = {}
    worker.finished.connect(lambda f, n: captured.update(frames=f))
    worker.run()
    assert len(captured["frames"]) == 4
    # Composited lazily: no dict of every frame's pixels is built up front.
    assert reads["n"] > 0


# --------------------------------------------------------------------------- #
# RGB convention handling on both canvases.
#
# Three producers, three conventions: make_composite yields float 0-1,
# make_magnetogram_composite and the overlay compositor yield uint8 0-255, and a
# float array carrying 0-255 turns up when a composite is passed through
# float-casting helpers. pyqtgraph raises on float without levels; matplotlib
# silently clips float 0-255 to white. Both must render identically.
# --------------------------------------------------------------------------- #
def _rgb_case(dtype, high, mid):
    arr = np.zeros((8, 8, 3), dtype)
    arr[..., 0] = high
    arr[..., 1] = mid
    return arr


RGB_CASES = [
    ("uint8_0_255", _rgb_case(np.uint8, 255, 128)),
    ("float_0_1", _rgb_case(float, 1.0, 128 / 255.0)),
    ("float_0_255", _rgb_case(float, 255.0, 128.0)),
]


@pytest.mark.parametrize("label, image", RGB_CASES)
def test_pyqtgraph_canvas_renders_every_rgb_convention(label, image):
    _app()
    from src.UI.sunpy_plot_window import SunPyPlotCanvas

    canvas = SunPyPlotCanvas()
    # A greyscale frame first, so stale contrast levels are in play.
    canvas.plot_map_data(np.random.rand(8, 8) * 3000, "grey", vmin=0, vmax=3000)
    canvas.plot_map_data(image, label)
    canvas.map_image.render()   # regression: this raised for float input

    colour = canvas.map_image.qimage.pixelColor(4, 4)
    assert (colour.red(), colour.green(), colour.blue()) == (255, 128, 0)
    assert list(canvas.map_image.levels) == [0, 255]


@pytest.mark.parametrize("label, image", RGB_CASES)
def test_matplotlib_canvas_renders_every_rgb_convention(label, image):
    _app()
    canvas = solar_mod.SolarMatplotlibCanvas()
    canvas.plot_map_data(image, label)
    canvas.figure.canvas.draw()

    rendered = canvas._image_artist.make_image(canvas.figure.canvas.get_renderer())[0]
    assert tuple(int(v) for v in rendered[4, 4][:3]) == (255, 128, 0)


def test_rgb_to_uint8_leaves_uint8_untouched():
    from src.UI.sunpy_plot_window import _rgb_to_uint8

    arr = _rgb_case(np.uint8, 255, 128)
    assert _rgb_to_uint8(arr) is arr


def test_rgb_to_uint8_handles_nan():
    from src.UI.sunpy_plot_window import _rgb_to_uint8

    arr = _rgb_case(float, 1.0, 0.5)
    arr[2, 2, :] = np.nan
    out = _rgb_to_uint8(arr)
    assert out.dtype == np.uint8
    assert tuple(out[2, 2]) == (0, 0, 0)


def test_float_composite_from_make_composite_reaches_the_canvas():
    """The Composite button produces float 0-1 RGB; it must still render."""
    _app()
    from src.Backend.solar_data_analysis import AiaCompositeSpec, make_composite
    from src.UI.sunpy_plot_window import SunPyPlotCanvas

    yy, xx = np.mgrid[0:32, 0:32]
    frames = [FakeMap(np.sin(xx / 3.0 + k) + 2.0) for k in range(3)]
    composite = make_composite(frames, AiaCompositeSpec(frame_indexes=(0, 1, 2)))
    assert composite.data.dtype.kind == "f"   # documents the convention

    canvas = SunPyPlotCanvas()
    canvas.plot_map_data(np.random.rand(32, 32) * 100, "grey", vmin=0, vmax=100)
    canvas.plot_map_data(composite.data, "composite")
    canvas.map_image.render()
    assert canvas.map_image.image.dtype == np.uint8


# --------------------------------------------------------------------------- #
# autoDownsample + RGB.
#
# These MUST use a shown canvas smaller than the image. ImageItem.render()
# returns early when _computeDownsampleFactors() finds no real viewport, so a
# canvas that was never sized silently skips the downsample path — which is
# exactly where the bug lived, and why earlier direct render() tests passed.
# --------------------------------------------------------------------------- #
def _downsampling_canvas(size=240):
    from src.UI.sunpy_plot_window import SunPyPlotCanvas

    canvas = SunPyPlotCanvas()
    canvas.resize(size, size)
    canvas.show()
    QApplication.processEvents()
    return canvas


@pytest.mark.parametrize("label, image", [
    ("uint8", (lambda: np.dstack([np.full((1024, 1024), v, np.uint8) for v in (200, 100, 0)]))()),
    ("float_0_1", (lambda: np.dstack([np.full((1024, 1024), v, float) for v in (0.8, 0.4, 0.0)]))()),
])
def test_rgb_survives_autodownsample(label, image):
    """Regression: downsampling averages, so uint8 RGB becomes float64.

    With levels=None that reached makeARGB(), which raises
    "levels argument is required for float input types" from inside paint().
    """
    _app()
    canvas = _downsampling_canvas()
    # A greyscale frame first, so stale levels would also be in play.
    canvas.plot_map_data(np.random.rand(1024, 1024).astype(np.float32) * 3000,
                         "grey", vmin=0, vmax=3000)
    QApplication.processEvents()
    canvas.plot_map_data(image, label)
    QApplication.processEvents()

    item = canvas.map_image
    assert item.autoDownsample is True
    xds, yds = item._computeDownsampleFactors()
    assert xds and xds > 1, "viewport must be smaller than the image to exercise this"

    item._renderRequired = True
    item.render()   # raised before the fix

    # Identity mapping for uint8 RGB, and correct after downsampling too.
    assert list(item.levels) == [0, 255]
    canvas.close()


def test_greyscale_still_gets_its_own_levels_after_an_rgb_frame():
    _app()
    canvas = _downsampling_canvas()
    rgb = np.dstack([np.full((1024, 1024), v, np.uint8) for v in (200, 100, 0)])
    canvas.plot_map_data(rgb, "rgb")
    QApplication.processEvents()
    canvas.plot_map_data(np.random.rand(1024, 1024).astype(np.float32) * 3000,
                         "grey", vmin=0.0, vmax=3000.0)
    QApplication.processEvents()

    item = canvas.map_image
    item._renderRequired = True
    item.render()
    assert list(item.levels) == [0.0, 3000.0]
    canvas.close()


def test_new_layer_is_seeded_with_a_readable_stretch():
    """A layer added from the UI must be visible without the user tuning it."""
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(_coronagraph_frames(), paths=[], metadata={})
    idx = win.overlay_add_combo.findText("STEREO-A/EUVI 195 A")
    win.overlay_add_combo.setCurrentIndex(idx)
    win.add_overlay_layer()

    spec = win._overlay_specs[0]
    assert spec.scale == "log"
    assert spec.gamma == pytest.approx(0.5)
    assert win.overlay_gamma_slider.value() == 50
    win.close()


def test_gamma_slider_writes_back_to_the_selected_layer():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(_coronagraph_frames(), paths=[], metadata={})
    win.overlay_add_combo.setCurrentIndex(win.overlay_add_combo.findText("AIA 193 A"))
    win.add_overlay_layer()

    win.overlay_gamma_slider.setValue(80)
    assert win._overlay_specs[0].gamma == pytest.approx(0.8)
    win.close()


def test_title_names_the_layers_actually_composited():
    """Regression: the title read the live spec list, so editing or clearing
    the stack after a build made it claim "+ 0 overlay layer(s)" over a
    composite that plainly showed one."""
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(_coronagraph_frames(), paths=[], metadata={})
    win.overlay_add_combo.setCurrentIndex(win.overlay_add_combo.findText("AIA 193 A"))
    win.add_overlay_layer()

    win._composite_pending_labels = ["AIA 193 A"]
    win._on_composite_finished(_rgb_composites(), [])
    assert "AIA 193 A" in win.plot_title_label.text()

    # Emptying the stack must not rewrite the title of the composite on screen.
    win._overlay_specs = []
    win._render_current_frame()
    assert "AIA 193 A" in win.plot_title_label.text()
    assert "0 overlay" not in win.plot_title_label.text()
    win.close()


def test_clearing_the_composite_drops_its_labels():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(_coronagraph_frames(), paths=[], metadata={})
    win._composite_pending_labels = ["AIA 193 A"]
    win._on_composite_finished(_rgb_composites(), [])
    win.clear_composite()
    assert win._composite_labels == []
    assert "AIA 193 A" not in win.plot_title_label.text()
    win.close()


def test_partial_overlap_is_reported(monkeypatch):
    """A large observer separation yields a crescent, not a disk. Say so."""
    from src.Backend.coronagraph_composite import LayerSpec

    base_frames = _coronagraph_frames(count=2, size=64)
    layer_frames = _coronagraph_frames(count=1, size=64)

    monkeypatch.setattr(
        solar_mod.CompositeBuildWorker, "_layer_sources",
        lambda self, spec, base_times: (["l0.fits"], [frame_observation_time(layer_frames[0])]),
    )
    monkeypatch.setattr(
        solar_mod.CompositeBuildWorker, "_load_one_layer_frame",
        lambda self, path, spec: layer_frames[0],
    )

    # Reprojection that only covers a sliver, as a big baseline would give.
    def sliver(source, target, **kw):
        out = _coronagraph_frames(count=1, size=64)[0]
        data = np.full((64, 64), np.nan)
        data[:, :8] = 100.0
        out.data = data
        return out

    monkeypatch.setattr("src.Backend.multiview.reproject_map_to", sliver)
    monkeypatch.setattr("src.Backend.multiview.observer_separation_deg", lambda a, b: 64.0)

    worker = solar_mod.CompositeBuildWorker(
        base_frames,
        LayerSpec("LASCO", "C2", "C2", colormap="soholasco2", scale="linear"),
        [LayerSpec("SECCHI", ("STEREO_A", "EUVI", 195.0), "EUVI 195",
                   inner_rsun=0.0, outer_rsun=1.7)],
        window_minutes=240,
    )
    captured = {}
    worker.finished.connect(lambda f, n: captured.update(frames=f, notes=n))
    worker.run()

    notes = captured["notes"]
    assert any("overlaps the base view" in n and "partial disk" in n for n in notes)
