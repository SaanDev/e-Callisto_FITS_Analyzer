"""
Linear/Log frequency axis for the dynamic spectrum.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("astropy")
pytest.importorskip("matplotlib")

from astropy.io import fits
from PySide6.QtWidgets import QApplication

from src.Backend.frequency_axis import (
    format_frequency_mhz,
    pyqtgraph_extent,
    log_frequency_ticks_mhz,
    log_frequency_rows,
    resample_row_mask_to_log_frequency,
    resample_rows_to_log_frequency,
)
from src.UI.main_window import FREQ_AXIS_LINEAR, FREQ_AXIS_LOG, MainWindow
from src.UI.widgets.collapsible_sections import collapsible_sections

NROWS = 60
NCOLS = 40


def _app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def spectrum(tmp_path):
    """A file whose three equal row bands make axis warping measurable."""
    header = fits.Header()
    header["TIME-OBS"] = "10:15:00"
    header["DATE-OBS"] = "2024-01-02"
    header["FREQMIN"] = 10.0
    header["FREQMAX"] = 100.0
    header["CRVAL1"] = 0.0
    header["CRPIX1"] = 1.0
    header["CDELT1"] = 22.5
    data = np.zeros((NROWS, NCOLS), dtype=np.uint8)
    data[: NROWS // 3] = 220
    data[NROWS // 3: 2 * NROWS // 3] = 130
    data[2 * NROWS // 3:] = 40
    path = tmp_path / "BIR_20240102_101500_01.fit"
    fits.PrimaryHDU(data=data, header=header).writeto(path, overwrite=True)
    return str(path)


@pytest.fixture
def window(spectrum):
    _app()
    win = MainWindow(theme=None)
    win.load_fits_into_main(spectrum)
    for _ in range(20):
        QApplication.processEvents()
    yield win
    win.close()


# -----------------------------
# Backend resampling
# -----------------------------
def test_log_rows_span_the_frequency_range_and_keep_direction():
    descending = log_frequency_rows(np.linspace(100.0, 10.0, 50))
    assert descending[0] > descending[-1]
    assert 10.0 ** descending[0] == pytest.approx(100.0)
    assert 10.0 ** descending[-1] == pytest.approx(10.0)

    ascending = log_frequency_rows(np.linspace(10.0, 100.0, 50))
    assert ascending[0] < ascending[-1]


def test_log_rows_are_uniform_in_log_space():
    rows = log_frequency_rows(np.linspace(100.0, 10.0, 40))
    steps = np.diff(rows)
    assert np.allclose(steps, steps[0])


def test_resampling_puts_each_frequency_in_the_right_band():
    freqs = np.linspace(100.0, 10.0, 90)
    data = np.zeros((90, 4))
    data[:30] = 2.0     # 100..70 MHz
    data[30:60] = 1.0   # 70..40
    data[60:] = 0.0     # 40..10

    out, rows = resample_rows_to_log_frequency(data, freqs)

    assert out.shape == data.shape
    for mhz, expected in ((90.0, 2.0), (75.0, 2.0), (60.0, 1.0), (45.0, 1.0), (30.0, 0.0), (15.0, 0.0)):
        row = int(np.argmin(np.abs(10.0 ** rows - mhz)))
        assert out[row, 0] == pytest.approx(expected)


def test_resampling_rejects_a_non_positive_axis():
    with pytest.raises(ValueError):
        resample_rows_to_log_frequency(np.zeros((3, 2)), np.array([0.0, -1.0, -2.0]))


def test_resampling_rejects_a_mismatched_axis():
    with pytest.raises(ValueError):
        resample_rows_to_log_frequency(np.zeros((3, 2)), np.array([1.0, 2.0]))


# -----------------------------
# The sidebar section
# -----------------------------
def test_axis_section_sits_in_the_sidebar_with_two_choices():
    _app()
    win = MainWindow(theme=None)
    try:
        titles = [section.title() for section in collapsible_sections(win.side_scroll.widget())]
        assert "Axis" in titles

        assert win.axis_linear_radio.text() == "Linear"
        assert win.axis_log_radio.text() == "Log"
        assert win.axis_linear_radio.isChecked() is True
        assert win.frequency_axis_scale() == FREQ_AXIS_LINEAR
    finally:
        win.close()


def test_choosing_log_switches_the_matplotlib_axis(window):
    assert window.canvas.ax.get_yscale() == "linear"

    window.axis_log_radio.setChecked(True)
    for _ in range(20):
        QApplication.processEvents()

    assert window.frequency_axis_scale() == FREQ_AXIS_LOG
    assert window.canvas.ax.get_yscale() == "log"


def test_choosing_linear_switches_back(window):
    window.axis_log_radio.setChecked(True)
    for _ in range(20):
        QApplication.processEvents()
    window.axis_linear_radio.setChecked(True)
    for _ in range(20):
        QApplication.processEvents()

    assert window.frequency_axis_scale() == FREQ_AXIS_LINEAR
    assert window.canvas.ax.get_yscale() == "linear"


def test_log_limits_stay_positive(window):
    """The extent's bottom edge sits half a channel below the lowest frequency,
    which can reach zero and would break a log axis."""
    window.axis_log_radio.setChecked(True)
    for _ in range(20):
        QApplication.processEvents()

    low, high = window.canvas.ax.get_ylim()
    assert low > 0.0
    assert high > low


def test_the_choice_is_remembered_across_a_restart(spectrum):
    _app()
    first = MainWindow(theme=None)
    try:
        first.axis_log_radio.setChecked(True)
        QApplication.processEvents()
    finally:
        first.close()

    second = MainWindow(theme=None)
    try:
        assert second.frequency_axis_scale() == FREQ_AXIS_LOG
        assert second.axis_log_radio.isChecked() is True
    finally:
        second.close()


def test_log_is_refused_without_a_positive_frequency_range(window):
    window.freqs = np.array([0.0, 0.0, 0.0])

    assert window._frequency_axis_log_usable() is False
    window.axis_log_radio.setChecked(True)
    QApplication.processEvents()

    # The preference is recorded, but the axis is left alone.
    assert window.frequency_axis_scale() == FREQ_AXIS_LOG
    assert window.canvas.ax.get_yscale() == "linear"


# -----------------------------
# The accelerated renderer
# -----------------------------
@pytest.fixture
def accel():
    pytest.importorskip("pyqtgraph")
    _app()
    from src.UI.accelerated_plot_widget import AcceleratedPlotWidget

    widget = AcceleratedPlotWidget()
    if not widget.is_available:
        pytest.skip("pyqtgraph renderer unavailable")
    return widget


def _cmap():
    import matplotlib

    return matplotlib.colormaps["viridis"]


def _fill(widget, *, log: bool):
    """Feed the widget the way the window does, extent included.

    The extent direction matters: ``pyqtgraph_extent`` hands back a descending
    y range for a descending CALLISTO frequency axis, and that is what tells
    the renderer which end of the image is the high-frequency one.
    """
    freqs = np.linspace(100.0, 10.0, NROWS)
    time = np.linspace(0.0, 900.0, NCOLS)
    data = np.zeros((NROWS, NCOLS), dtype=np.float32)
    data[: NROWS // 3] = 220.0
    data[NROWS // 3: 2 * NROWS // 3] = 130.0
    data[2 * NROWS // 3:] = 40.0
    widget.update_image(
        data,
        extent=pyqtgraph_extent(freqs, time),
        cmap=_cmap(),
        freqs=freqs,
        log_freq=log,
    )
    return freqs, data


def test_accel_resamples_rows_so_frequencies_land_correctly(accel):
    _fill(accel, log=True)
    assert accel._log_freq is True

    img = accel._image.image
    rows = img.shape[0]
    lo, hi = np.log10(10.0), np.log10(100.0)
    for mhz, expected in ((90.0, 220.0), (60.0, 130.0), (20.0, 40.0)):
        frac = (np.log10(mhz) - lo) / (hi - lo)
        row = int(round((1.0 - frac) * (rows - 1)))
        assert img[row, 0] == pytest.approx(expected)


def test_accel_labels_the_log_axis_in_megahertz(accel):
    _fill(accel, log=True)

    levels = accel._plot.getAxis("left")._tickLevels
    assert levels is not None
    labels = dict(levels[0])
    assert pytest.approx(1.0) in [round(p, 6) for p in labels]
    assert labels[1.0] == "10"
    assert labels[2.0] == "100"


def test_accel_restores_automatic_ticks_in_linear_mode(accel):
    _fill(accel, log=True)
    _fill(accel, log=False)

    assert accel._log_freq is False
    assert accel._plot.getAxis("left")._tickLevels is None


def test_accel_view_round_trips_in_megahertz(accel):
    _fill(accel, log=True)

    # The widget works internally in log10 but its API is in MHz, and it frames
    # the same channel edges the linear branch does.
    _x0, _x1, edge_hi, edge_lo = pyqtgraph_extent(
        np.linspace(100.0, 10.0, NROWS), np.linspace(0.0, 900.0, NCOLS)
    )
    assert accel.get_view()["ylim"] == pytest.approx((edge_lo, edge_hi))

    accel.set_view({"xlim": (0.0, 900.0), "ylim": (20.0, 80.0)})
    assert accel.get_view()["ylim"] == pytest.approx((20.0, 80.0))


def test_accel_overlays_are_given_in_megahertz_and_drawn_in_log_space(accel):
    _fill(accel, log=True)

    accel.show_measurement([(10.0, 50.0), (80.0, 70.0)], label="m")
    ys = accel._measurement_scatter_item.getData()[1]
    assert list(np.round(ys, 6)) == [pytest.approx(np.log10(50.0)), pytest.approx(np.log10(70.0))]

    accel.show_drift_points([(10.0, 50.0)], with_segments=False)
    assert accel._drift_scatter_item.getData()[1][0] == pytest.approx(np.log10(50.0))

    accel.set_light_curve_overlay([0.0, 50.0], [60.0, 62.0])
    ys = accel._light_curve_items[0].getData()[1]
    assert list(np.round(ys, 6)) == [pytest.approx(np.log10(60.0)), pytest.approx(np.log10(62.0))]


def test_accel_overlays_are_untouched_in_linear_mode(accel):
    _fill(accel, log=False)

    accel.show_measurement([(10.0, 50.0), (80.0, 70.0)], label="m")
    assert list(accel._measurement_scatter_item.getData()[1]) == [50.0, 70.0]


def test_accel_mouse_coordinates_come_back_as_megahertz(accel):
    _fill(accel, log=True)

    # _scene_to_plot_xy is the single place mouse coordinates leave the widget.
    assert accel._data_y_scalar(accel._axis_y_scalar(64.0)) == pytest.approx(64.0)
    assert accel._axis_y_scalar(64.0) == pytest.approx(np.log10(64.0))


# -----------------------------
# Orientation
#
# The first version of the log axis drew the spectrum upside down: the
# accelerated renderer built its image rectangle from min/max of the log rows,
# which throws away the descending direction a CALLISTO frequency axis has.
# These tests pin the placement, not just the pixel values -- the earlier tests
# checked the re-sampled array and so never saw where it was put.
# -----------------------------
def _row_y(widget, row: float) -> float:
    """Where an image row lands on the plot's y axis."""
    from PySide6.QtCore import QPointF

    return float(widget._image.mapToView(QPointF(0.0, float(row))).y())


def test_accel_log_keeps_the_spectrum_the_right_way_up(accel):
    _fill(accel, log=False)
    linear_top = _row_y(accel, 0.0)
    assert linear_top > _row_y(accel, float(accel._image.image.shape[0]))

    _fill(accel, log=True)
    log_top = _row_y(accel, 0.0)
    assert log_top > _row_y(accel, float(accel._image.image.shape[0]))

    # Row 0 frames the same frequency in both scales -- the two renderers have
    # to agree on the band, or "Reset Selection" never settles.
    assert accel._data_y_scalar(log_top) == pytest.approx(linear_top)


def test_accel_log_draws_the_high_frequency_band_at_the_top(accel):
    _fill(accel, log=True)

    image = accel._image.image
    last = image.shape[0] - 1
    top_row = 0 if _row_y(accel, 0.5) > _row_y(accel, last + 0.5) else last
    bottom_row = last - top_row

    assert image[top_row, 0] == pytest.approx(220.0)   # 100-70 MHz
    assert image[bottom_row, 0] == pytest.approx(40.0)  # 40-10 MHz


def test_accel_log_moves_the_gap_mask_with_the_rows(accel):
    freqs = np.linspace(100.0, 10.0, NROWS)
    data = np.zeros((NROWS, NCOLS), dtype=np.float32)
    mask = np.zeros(NROWS, dtype=bool)
    mask[:5] = True  # the top five channels, 100 MHz and just below

    accel.update_image(
        data,
        extent=pyqtgraph_extent(freqs, np.linspace(0.0, 900.0, NCOLS)),
        cmap=_cmap(),
        gap_row_mask=mask,
        freqs=freqs,
        log_freq=True,
    )

    moved = resample_row_mask_to_log_frequency(mask, freqs)
    assert moved is not None
    # A gap at the top of a linear axis is a much thinner band once the rows
    # are spread logarithmically, but it stays at the top.
    assert bool(moved[0]) is True
    assert bool(moved[-1]) is False
    assert int(moved.sum()) < int(mask.sum())


def test_matplotlib_log_keeps_high_frequencies_at_the_top(window):
    window.axis_log_radio.setChecked(True)
    for _ in range(20):
        QApplication.processEvents()

    ax = window.canvas.ax
    low, high = ax.get_ylim()
    assert high > low

    image = ax.images[0]
    x0, x1, y0, y1 = image.get_extent()
    assert y1 > y0
    assert image.origin == "upper"  # so row 0, the highest frequency, is at y1

    # And the axis itself runs the right way in rendered coordinates.
    assert ax.transData.transform((0.0, 90.0))[1] > ax.transData.transform((0.0, 20.0))[1]


# -----------------------------
# Base-10 ticks
#
# The axis used to be labelled by halving decades, which put ticks at 32 and
# 64 MHz on a 20-80 MHz band and at 316 MHz on 45-870; matplotlib's own decade
# locator was worse, leaving a band with no decade inside it unlabelled.
# -----------------------------
@pytest.mark.parametrize(
    "low, high, expected",
    [
        (20.0, 80.0, ["20", "30", "40", "50", "60", "70", "80"]),
        (45.0, 870.0, ["50", "70", "100", "200", "300", "500", "700"]),
        (175.0, 870.0, ["200", "300", "400", "500", "600", "700", "800"]),
        (0.02, 13.825, ["0.02", "0.05", "0.1", "0.2", "0.5", "1", "2", "5", "10"]),
    ],
)
def test_log_ticks_are_anchored_to_the_decades(low, high, expected):
    labelled, _minor = log_frequency_ticks_mhz(low, high)
    assert [format_frequency_mhz(v) for v in labelled] == expected


def test_log_ticks_fall_back_to_round_numbers_below_one_decade_step():
    """45-55 MHz holds no decade subdivision at all, so linear-chosen ticks are
    the only honest option; they are still placed logarithmically."""
    labelled, _minor = log_frequency_ticks_mhz(45.0, 55.0)
    assert len(labelled) >= 3
    assert all(45.0 <= v <= 55.0 for v in labelled)


def test_log_ticks_stay_inside_the_band():
    for low, high in ((20.0, 80.0), (45.0, 870.0), (0.02, 13.825)):
        labelled, minor = log_frequency_ticks_mhz(low, high)
        for value in list(labelled) + list(minor):
            assert low * 0.999 <= value <= high * 1.001


def test_log_ticks_refuse_a_non_positive_band():
    assert log_frequency_ticks_mhz(0.0, 100.0) == ([], [])
    assert log_frequency_ticks_mhz(-5.0, -1.0) == ([], [])
    assert log_frequency_ticks_mhz(50.0, 50.0) == ([], [])


def test_minor_ticks_never_repeat_a_label():
    labelled, minor = log_frequency_ticks_mhz(45.0, 870.0)
    assert not (set(labelled) & set(minor))


def test_accel_log_ticks_are_decade_anchored(accel):
    freqs = np.linspace(870.0, 45.0, NROWS)
    accel.update_image(
        np.zeros((NROWS, NCOLS), dtype=np.float32),
        extent=pyqtgraph_extent(freqs, np.linspace(0.0, 900.0, NCOLS)),
        cmap=_cmap(),
        freqs=freqs,
        log_freq=True,
    )

    levels = accel._plot.getAxis("left")._tickLevels
    assert levels is not None and len(levels) == 2
    labels = [text for _pos, text in levels[0]]
    assert labels == ["50", "70", "100", "200", "300", "500", "700"]
    # Positions are log10 of the frequency they name.
    for position, text in levels[0]:
        assert 10.0 ** position == pytest.approx(float(text), rel=1e-6)
    assert all(text == "" for _pos, text in levels[1])


def test_matplotlib_labels_a_band_that_contains_no_decade(window):
    """The fixture spans 10-100 MHz; narrow it to a band with no decade in it."""
    window.freqs = np.linspace(79.0, 45.0, NROWS)
    window.axis_log_radio.setChecked(True)
    for _ in range(20):
        QApplication.processEvents()
    window.plot_data(window.raw_data, title="Raw")
    for _ in range(20):
        QApplication.processEvents()

    ax = window.canvas.ax
    assert ax.get_yscale() == "log"
    low, high = ax.get_ylim()
    ticks = [t for t in ax.yaxis.get_majorticklocs() if low <= t <= high]
    assert len(ticks) >= 3
    assert [t.get_text() for t in ax.get_yticklabels() if t.get_text()]


def test_switching_back_to_linear_restores_the_default_ticks(window):
    window.axis_log_radio.setChecked(True)
    for _ in range(20):
        QApplication.processEvents()
    window.axis_linear_radio.setChecked(True)
    for _ in range(20):
        QApplication.processEvents()

    ax = window.canvas.ax
    assert ax.get_yscale() == "linear"
    # A FixedLocator left over from log mode would freeze the linear ticks.
    from matplotlib.ticker import FixedLocator

    assert not isinstance(ax.yaxis.get_major_locator(), FixedLocator)


def test_accel_restores_automatic_ticks_when_log_is_switched_off(accel):
    _fill(accel, log=True)
    assert accel._plot.getAxis("left")._tickLevels is not None
    _fill(accel, log=False)
    assert accel._plot.getAxis("left")._tickLevels is None


def test_both_renderers_frame_the_same_band_in_log_mode(window):
    """The home view comes from the matplotlib axis but is restored onto
    whichever canvas is live, so the two have to describe the same band."""
    window.axis_log_radio.setChecked(True)
    for _ in range(30):
        QApplication.processEvents()

    home = window._home_view
    current = window._capture_view()
    assert current["ylim"] == pytest.approx(tuple(home["ylim"]))


def test_log_view_round_trips_through_the_window(window):
    window.axis_log_radio.setChecked(True)
    for _ in range(30):
        QApplication.processEvents()

    window._restore_view({"xlim": (100.0, 300.0), "ylim": (20.0, 60.0)})
    for _ in range(10):
        QApplication.processEvents()
    assert window._capture_view()["ylim"] == pytest.approx((20.0, 60.0))

    window._restore_view(window._home_view)
    for _ in range(10):
        QApplication.processEvents()
    assert window._capture_view()["ylim"] == pytest.approx(tuple(window._home_view["ylim"]))


def test_accel_log_survives_a_band_whose_bottom_edge_reaches_zero(accel):
    """Half a channel below the lowest frequency can be zero or negative, which
    no log axis can frame; the channel centres carry it instead."""
    freqs = np.linspace(6.0, 2.0, NROWS)
    time = np.linspace(0.0, 900.0, NCOLS)
    data = np.zeros((NROWS, NCOLS), dtype=np.float32)
    data[: NROWS // 2] = 200.0

    extent = list(pyqtgraph_extent(freqs, time))
    extent[3] = -0.5  # a bottom edge a log axis cannot take
    accel.update_image(data, extent=extent, cmap=_cmap(), freqs=freqs, log_freq=True)

    assert accel._log_freq is True
    low, high = accel.get_view()["ylim"]
    assert low > 0.0 and high > low
    # And still the right way up.
    assert _row_y(accel, 0.0) > _row_y(accel, float(accel._image.image.shape[0]))
