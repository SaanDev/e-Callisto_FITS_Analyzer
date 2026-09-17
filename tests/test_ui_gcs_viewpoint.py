"""Scientific timestamp, projection and async request invariants for a viewpoint."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("sunpy.map")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.Backend import helioviewer_jp2 as hvjp2
from src.UI.gcs_viewpoint_panel import GCSViewpointPanel, JP2FetchWorker, _FetchDelivery


def _frames(count=3, *, moving_observer=False):
    import astropy.units as u
    import sunpy.map
    from astropy.coordinates import SkyCoord
    from sunpy.coordinates import frames
    from sunpy.map.header_helper import make_fitswcs_header

    result = []
    for index in range(count):
        stamp = datetime(2012, 7, 12, 16) + timedelta(minutes=12 * index)
        observer = SkyCoord((index if moving_observer else 0) * u.deg, 0 * u.deg,
                            (1 + index * 0.01 if moving_observer else 1) * u.AU,
                            frame=frames.HeliographicStonyhurst, obstime=stamp)
        reference = SkyCoord(0 * u.arcsec, 0 * u.arcsec, frame="helioprojective",
                             observer=observer, obstime=stamp)
        yy, xx = np.mgrid[:32, :32]
        data = (10 + index + np.exp(-((xx - 12 - index) ** 2 + (yy - 16) ** 2) / 4)).astype(np.float32)
        header = make_fitswcs_header(data, reference, scale=[200, 200] * u.arcsec / u.pix)
        result.append(sunpy.map.Map(data, header))
    return result


@pytest.fixture
def panel():
    app = QApplication.instance() or QApplication([])
    widget = GCSViewpointPanel("A")
    yield widget
    widget.shutdown(wait_ms=0)
    widget.close()
    app.processEvents()


def test_first_running_frame_never_displays_a_future_front(panel):
    frames = _frames()
    panel.set_frames(frames, harmonised=True)
    assert np.array_equal(panel.display_array(0), frames[0].data)
    assert panel.current_time() == datetime(2012, 7, 12, 16)
    assert "no earlier frame; showing raw" in panel.title_label.text()
    assert panel.canvas._last_map_levels[0] > 0
    assert panel.rendered_mode() == "raw"


def test_base_reference_frame_is_visible_and_explicitly_raw(panel):
    frames = _frames()
    panel.set_frames(frames, harmonised=True)
    panel.set_difference_mode("base")
    assert np.array_equal(panel.display_array(0), frames[0].data)
    assert panel.rendered_mode() == "raw"
    assert "Base reference frame — showing raw" in panel.title_label.text()


def test_running_difference_names_both_observation_and_reference_times(panel):
    panel.set_frames(_frames(), harmonised=True)
    panel.set_shared_time(datetime(2012, 7, 12, 16, 12))
    caption = panel.title_label.text()
    assert "2012-07-12 16:12:00 UTC" in caption
    assert "reference 2012-07-12 16:00:00 UTC" in caption
    assert panel.canvas._last_map_levels[0] == -panel.canvas._last_map_levels[1]
    assert panel.rendered_mode() == "running"


def test_observer_and_limb_follow_the_selected_frame(panel):
    panel.set_frames(_frames(moving_observer=True), harmonised=True)
    first_radius = panel.observer.rsun_arcsec
    panel.set_shared_time(datetime(2012, 7, 12, 16, 24))
    assert panel.observer.lon_deg == pytest.approx(2)
    assert panel.observer.rsun_arcsec < first_radius
    assert panel.observer.obstime == panel.current_time()


def test_timezone_aware_range_and_target_are_converted_to_utc(panel):
    local = timezone(timedelta(hours=5, minutes=30))
    panel.set_date_range(datetime(2012, 7, 12, 21, 30, tzinfo=local),
                         datetime(2012, 7, 12, 22, 30, tzinfo=local))
    assert panel.date_range() == (datetime(2012, 7, 12, 16), datetime(2012, 7, 12, 17))
    panel.set_frames(_frames(), harmonised=True)
    panel.set_shared_time(datetime(2012, 7, 12, 21, 42, tzinfo=local))
    assert panel.current_time() == datetime(2012, 7, 12, 16, 12)
    assert panel.is_synchronized()


def test_offset_and_tolerance_do_not_claim_a_distant_frame_is_simultaneous(panel):
    panel.set_frames(_frames(), harmonised=True)
    panel.set_shared_time(datetime(2012, 7, 12, 16, 6))
    assert panel.time_offset_seconds() == -360
    assert not panel.is_synchronized()
    assert "Δt -6.0 min" in panel.title_label.text()
    assert "outside tolerance" in panel.title_label.text()
    panel.set_sync_tolerance(360)
    assert panel.is_synchronized()


def test_a_difference_failure_does_not_contaminate_other_cached_frames(panel, monkeypatch):
    frames = _frames()
    real_difference = hvjp2.difference_image

    def difference(current, reference):
        if current is frames[1]:
            raise ValueError("bad reference")
        return real_difference(current, reference)

    monkeypatch.setattr(hvjp2, "difference_image", difference)
    panel.set_frames(frames, harmonised=True)
    panel.set_shared_time(datetime(2012, 7, 12, 16, 12))
    assert "failed — showing raw" in panel.title_label.text()
    assert panel.rendered_mode() == "raw"
    panel.set_shared_time(datetime(2012, 7, 12, 16, 24))
    assert "failed" not in panel.title_label.text()
    assert panel.canvas._last_map_levels[0] < 0
    panel.set_shared_time(datetime(2012, 7, 12, 16, 12))
    assert "failed — showing raw" in panel.title_label.text()
    assert panel.canvas._last_map_levels[0] > 0


def test_empty_frames_clear_the_old_pixels_and_projection(panel):
    panel.set_frames(_frames(), harmonised=True)
    panel.set_frames([])
    assert panel.canvas.map_image.image is None
    assert panel.observer is None
    assert not panel.is_synchronized()


def test_source_and_range_changes_invalidate_previously_loaded_event(panel):
    panel.set_frames(_frames(), harmonised=True)
    panel.select_source("COR2-A")
    assert panel.frame_count() == 0
    panel.set_frames(_frames(), harmonised=True)
    panel.set_date_range(datetime(2012, 7, 12, 16), datetime(2012, 7, 12, 17))
    assert panel.frame_count() == 0


def test_setting_an_unchanged_range_keeps_current_frames(panel):
    start, end = datetime(2012, 7, 12, 16), datetime(2012, 7, 12, 17)
    panel.set_date_range(start, end)
    panel.set_frames(_frames(), harmonised=True)
    panel.set_date_range(start, end)
    assert panel.frame_count() == 3


def test_cancelled_old_source_cannot_replace_new_frames(panel, monkeypatch, tmp_path):
    panel._cache_base = tmp_path
    panel.set_date_range(datetime(2012, 7, 12, 16), datetime(2012, 7, 12, 17))
    workers = []

    def launch(worker):
        workers.append(worker)
        panel._worker = worker

    monkeypatch.setattr(panel, "_launch", launch)
    assert panel.start_fetch()
    old = workers[-1]
    panel.select_source("COR2-A")
    assert old._cancel.is_set()
    fresh = _frames(count=1)
    panel.set_frames(fresh, harmonised=True)
    old.finished.emit(SimpleNamespace(frames=_frames()))
    assert panel.frames == fresh


def test_an_unhandled_rotation_cannot_draw_a_false_projection(panel):
    frame = _frames(count=1)[0]
    frame.meta["pc1_1"], frame.meta["pc1_2"] = 0, -1
    frame.meta["pc2_1"], frame.meta["pc2_2"] = 1, 0
    panel.set_frames([frame], harmonised=True)
    assert panel.observer is None
    assert "Projection unavailable" in panel.title_label.text()


def test_identically_sized_but_different_wcs_grids_are_not_subtracted(panel):
    frames = _frames(count=2)
    frames[1].meta["cdelt1"] *= 1.1
    panel.set_frames(frames, harmonised=True)
    panel.set_shared_time(datetime(2012, 7, 12, 16, 12))
    assert "failed — showing raw" in panel.title_label.text()
    assert np.array_equal(panel.display_array(1), frames[1].data)


def test_already_queued_completion_cannot_restore_invalidated_event(panel):
    worker = JP2FetchWorker(hvjp2.source_by_key("LASCO C2"), datetime(2012, 7, 12, 16),
                            datetime(2012, 7, 12, 17), max_frames=4, cache_dir=None)
    delivery = _FetchDelivery(panel, worker)
    worker.finished.connect(delivery.finished, Qt.QueuedConnection)
    panel._worker = worker
    worker.finished.emit(SimpleNamespace(frames=_frames()))
    panel.invalidate_frames()
    QApplication.processEvents()
    assert panel.frame_count() == 0
    assert panel.canvas.map_image.image is None
