import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from src.UI.matplotlib_widget import MplCanvas


def test_mpl_canvas_is_figure_canvas_subclass():
    assert issubclass(MplCanvas, FigureCanvas)
