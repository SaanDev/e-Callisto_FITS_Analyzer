"""Scientific parameter controls must display the same values as the model."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtWidgets import QApplication

from src.backend.gcs.gcs_model import GCSParameters
from src.ui.solar.solar_measure_tools import GCSParameterPanel


@pytest.fixture
def panel():
    app = QApplication.instance() or QApplication([])
    widget = GCSParameterPanel()
    yield widget
    widget.close()
    app.processEvents()


def test_echoing_a_refined_fit_does_not_quantize_or_clip_it(panel):
    params = GCSParameters(185.125, -12.123, 96.5, 40.123456, 32.0, 0.321234)
    changes = []
    panel.parametersChanged.connect(changes.append)
    panel.show_parameters(params)
    assert panel.parameters() == params
    for name, slider in panel.sliders.items():
        assert slider.value() == getattr(params, name)
        assert slider.readout.value() == pytest.approx(getattr(params, name), abs=0.00051)
    assert changes == []


def test_numeric_entry_updates_the_shared_parameters_once(panel):
    params = GCSParameters(35.0, -12.0, 25.0, 9.0, 32.0, 0.32)
    panel.show_parameters(params)
    changes = []
    panel.parametersChanged.connect(changes.append)
    panel.sliders["height_rsun"].readout.setValue(9.123)
    assert panel.parameters().height_rsun == pytest.approx(9.123)
    assert len(changes) == 1
    assert changes[0] == panel.parameters()


def test_derived_widths_include_the_shell_thickness(panel):
    panel.show_parameters(GCSParameters(35.0, -12.0, 25.0, 9.0, 30.0, 0.5))
    text = panel.derived_label.text()
    assert "face-on 120.0°" in text
    assert "edge-on 60.0°" in text
    assert "full width" not in text
