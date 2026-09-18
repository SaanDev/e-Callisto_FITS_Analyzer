"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Exports of the three-viewpoint GCS window (src/UI/gcs_window_exports.py) and the
tracking panel's graph export (src/UI/solar_measure_tools.py).

The load-bearing tests pin two things. Exports are drawn from the data, never
grabbed from the screen — with hardware acceleration on, a grab of the OpenGL
image panels comes back blank, which is what the old snapshot saved. And a movie
or report may step through time, but leaves the window exactly as it found it:
the time, the working models, their formal errors and the front points.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("sunpy.map")

from test_ui_gcs_window import _app, _flush, _sequence
from src.UI.gcs_fitting_window import GCSFittingWindow


@pytest.fixture
def window():
    _app()
    widget = GCSFittingWindow()
    for panel, lon in zip(widget.panels, (-60.0, 0.0, 60.0)):
        panel.set_frames(_sequence(lon, n=4))
    _flush()
    yield widget
    widget.close()


def _pixels(path):
    from PIL import Image

    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def _record(window, index, height=None, model="gcs"):
    window._show_time_index(index)
    if model != window.editing_model():
        window.set_editing_model(model)
    if height is not None:
        params = window.parameters() if model == "gcs" else window.shock_parameters()
        changed = params.replace_values(height_rsun=height)
        (window._on_parameters if model == "gcs" else window._on_shock_parameters)(changed)
    window._on_commit()


def _state(window):
    return (
        window.time_slider.value(),
        window._requested_time,
        window.parameters(),
        window.shock_parameters(),
        window._tracks["gcs"].last_refinement,
        {label: list(points) for label, points in window._tracks["gcs"].clicks.items()},
    )


# --- Snapshot -------------------------------------------------------------------


def test_snapshot_is_drawn_from_the_data_not_grabbed(window, tmp_path, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("a screen grab is blank for OpenGL panels")

    monkeypatch.setattr(window.stage, "grab", refuse)
    path = window.write_snapshot(tmp_path / "views.png")
    pixels = _pixels(path)
    assert pixels.shape[1] >= 3000  # 300 dpi
    assert len(np.unique(pixels.reshape(-1, 3), axis=0)) > 1000


@pytest.mark.parametrize("suffix", ["png", "pdf", "eps", "svg", "tiff", "jpg"])
def test_snapshot_saves_every_app_figure_format(window, tmp_path, suffix):
    path = window.write_snapshot(tmp_path / f"views.{suffix}")
    assert path.stat().st_size > 0


def test_export_views_match_the_panels_and_carry_the_models(window):
    panel = window.panels[0]
    window._on_canvas_click("A", 1500.0, 250.0, "left")
    views = window._export_views()
    assert [view.label for view in views] == ["A", "B", "C"]
    assert np.array_equal(views[0].data, panel.display_array(panel._index))
    # 48 pixels of 112 arcsec, centred on the Sun.
    x0, x1, y0, y1 = views[0].extent
    assert x1 - x0 == pytest.approx(48 * 112.0) and (x0 + x1) / 2 == pytest.approx(0.0, abs=1e-6)
    assert views[0].points == ((1500.0, 250.0),)
    assert views[0].gcs_xy is not None and views[0].shock_xy is None
    window.shock_check.setChecked(True)
    assert window._export_views()[0].shock_xy is not None


def test_hidden_banners_leave_only_the_panel_name_in_exports(window):
    assert "\n" in window._export_views()[0].caption  # time, offset and view mode lines
    window.banner_check.setChecked(False)
    captions = [view.caption for view in window._export_views()]
    assert captions == [panel.title_label.text().splitlines()[0] for panel in window.panels]
    assert all("\n" not in caption and caption.startswith(label) for caption, label in zip(captions, "ABC"))


# --- Movie ------------------------------------------------------------------------


def test_gif_has_a_frame_per_time_step_at_the_playback_rate(window, tmp_path):
    from PIL import Image

    path = window.write_movie(tmp_path / "movie.gif", fps=5.0)
    with Image.open(path) as image:
        assert image.n_frames == len(window._time_axis)
        assert image.info.get("duration") == 200
        assert image.info.get("loop") == 0


def test_mp4_export(window, tmp_path):
    pytest.importorskip("imageio_ffmpeg")
    path = window.write_movie(tmp_path / "movie.mp4", fps=4.0)
    assert path.stat().st_size > 0


def test_movie_leaves_the_window_as_it_was(window, tmp_path):
    _record(window, 0, height=6.0)
    _record(window, 3, height=12.0)
    window._show_time_index(1)
    # An uncommitted edit, a refinement and a front point — all of which stepping
    # through time would normally replace.
    window._on_parameters(window.parameters().replace_values(lon_deg=42.0))
    window._tracks["gcs"].last_refinement = SimpleNamespace(converged=True, parameters=window.parameters())
    window._on_canvas_click("B", 900.0, -300.0, "left")
    before = _state(window)

    window.write_movie(tmp_path / "movie.gif", fps=4.0)

    assert _state(window) == before
    assert window.parameters().lon_deg == pytest.approx(42.0)


def test_cancelled_movie_leaves_no_file(window, tmp_path):
    target = tmp_path / "movie.gif"
    assert window.write_movie(target, fps=4.0, cancel_cb=lambda: True) is None
    assert not target.exists()


def test_movie_needs_two_time_steps(tmp_path):
    _app()
    empty = GCSFittingWindow()
    try:
        with pytest.raises(ValueError):
            empty.write_movie(tmp_path / "movie.gif", fps=4.0)
    finally:
        empty.close()


# --- Report ---------------------------------------------------------------------


def test_report_draws_each_recorded_fit_on_its_own_images(window, tmp_path):
    _record(window, 0, height=6.0)
    _record(window, 2, height=10.0)
    _record(window, 2, height=11.0, model="shock")
    window.set_editing_model("gcs")
    before = _state(window)

    data = window._report_input()
    assert data.current_figure is not None
    assert sorted(data.fit_figures) == sorted(window._tracks["gcs"].fits)
    assert all(image for image in data.fit_figures.values())
    assert _state(window) == before

    result = window.write_report(tmp_path / "report.pdf")
    # On screen, the two recorded times, and the GCS linear graph: two GCS times
    # fit no quadratic or cubic, and one shock time fits nothing.
    assert result.figures_written == 4
    assert (tmp_path / "report.pdf").read_bytes().startswith(b"%PDF")
    assert _state(window) == before


def test_report_says_when_a_fits_images_are_gone(window):
    _record(window, 1, height=8.0)
    when = next(iter(window._tracks["gcs"].fits))
    # Reloading a channel replaces the images the fit was recorded on.
    window.panels[0].set_frames(_sequence(-60.0, n=4, start_min=6))
    _flush()
    data = window._report_input()
    assert data.fit_figures[when] is None
    assert "no longer loaded" in data.fit_figure_notes[when]


# --- Height–time graph ----------------------------------------------------------------


def test_tracking_panel_export_menu_offers_table_and_graph(window):
    menu = window.tracking_panel.export_btn.menu()
    texts = [action.text() for action in menu.actions()]
    assert texts == ["Table as CSV…", "Graph (PNG, PDF, EPS, SVG, TIFF, JPG)…"]


@pytest.mark.parametrize("suffix", ["png", "pdf", "eps", "svg", "tiff", "jpg"])
def test_graph_saves_every_app_figure_format(window, tmp_path, suffix):
    for index, height in ((0, 5.0), (1, 6.5), (2, 8.0)):
        _record(window, index, height=height)
    path = window.tracking_panel.write_graph(str(tmp_path / f"graph.{suffix}"))
    assert (tmp_path / f"graph.{suffix}").stat().st_size > 0
    if suffix == "png":
        assert tuple(_pixels(path)[3, 3]) == (255, 255, 255)  # light mode


def test_graph_follows_the_selected_fit_order_and_model(window):
    for index, height in ((0, 5.0), (1, 6.5), (2, 8.5)):
        _record(window, index, height=height)
    panel = window.tracking_panel
    panel.fit_order_combo.setCurrentIndex(panel.fit_order_combo.findData(2))
    series = panel.graph_series()
    assert series.order == 2 and series.heights_rsun == (5.0, 6.5, 8.5)
    axes = panel.graph_figure().axes[0]
    labels = [text.get_text() for text in axes.get_legend().get_texts()]
    assert labels[0] == "GCS apex (3-D)" and labels[1].startswith("Quadratic fit")
    assert axes.get_title() == "GCS flux-rope apex height–time: quadratic fit"
    window.set_editing_model("shock")
    assert window.tracking_panel.graph_series() is None  # the shock has nothing recorded


def test_plane_of_sky_tracking_graph_exports_too(tmp_path):
    """The Solar Image Analysis tracking panel uses the same Export menu."""
    from src.UI.solar_measure_tools import TrackingPanel

    _app()
    panel = TrackingPanel()
    base = datetime(2012, 7, 12, 16, 0, 0)
    picks = {index: (base + timedelta(minutes=12 * index), 2.5 + index, 0.0, 0.0, 270.0) for index in range(3)}
    panel.refresh(picks)
    assert panel.export_btn.isEnabled()
    panel.write_graph(str(tmp_path / "picks.svg"))
    assert (tmp_path / "picks.svg").stat().st_size > 0
    assert panel.graph_series().errors_rsun is None
    assert panel.graph_figure().axes[0].get_title() == "CME leading-edge height–time: linear fit"
    panel.close()


# --- Menus ----------------------------------------------------------------------


def test_file_menu_offers_the_exports_when_they_can_run(window):
    window._sync_menu_actions()
    actions = window.menu_actions
    for key in ("snapshot", "movie", "report", "export_graph"):
        assert key in actions
    assert actions["snapshot"].isEnabled() and actions["movie"].isEnabled() and actions["report"].isEnabled()
    assert not actions["export_graph"].isEnabled()  # nothing recorded yet
    _record(window, 0, height=6.0)
    window._sync_menu_actions()
    assert actions["export_graph"].isEnabled()
