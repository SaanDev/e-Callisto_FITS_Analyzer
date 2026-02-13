"""
e-CALLISTO FITS Analyzer
Version 2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from src.UI.matplotlib_widget import MplCanvas
from src.UI.mpl_style import apply_origin_style, style_axes
apply_origin_style()


def test_mpl_canvas_is_figure_canvas_subclass():
    assert issubclass(MplCanvas, FigureCanvas)
