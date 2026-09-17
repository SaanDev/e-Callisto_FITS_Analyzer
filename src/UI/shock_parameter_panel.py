"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Controls for the spheroid/ellipsoid shock model (src/UI/shock_parameter_panel.py).

The shock counterpart of ``GCSParameterPanel``: a shape selector, one slider with
numeric entry per PyThea parameter, a readout of the centre and semi-axes those
parameters imply, and Refine/Commit. Ranges match PyThea's defaults so a fit can
be carried between the two.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.Backend.shock_model import (
    ELLIPSOID,
    SHOCK_MODEL_LABELS,
    SHOCK_MODELS,
    SPHEROID,
    ShockParameters,
    shock_semi_axes,
)
from src.UI.solar_measure_tools import GCSParameterSlider

#: The shock the window starts from: just outside the default GCS shell's apex,
#: round, and wide enough to read as an envelope.
DEFAULT_SHOCK_HEIGHT_RSUN = 9.0
DEFAULT_SHOCK_KAPPA = 0.7


class ShockParameterPanel(QWidget):
    """Shape selector, the shock sliders, a derived-geometry readout and Refine/Commit.

    Emits ``parametersChanged`` on every slider move or shape change, and never
    when parameters are echoed in through :meth:`show_parameters`.
    """

    parametersChanged = Signal(object)
    refineRequested = Signal()
    commitRequested = Signal()

    #: ``name -> (label, minimum, maximum, unit, decimals, tooltip)``, in PyThea's ranges.
    _SPECS: tuple[tuple[str, str, float, float, str, int, str], ...] = (
        ("lon_deg", "Lon", -180.0, 180.0, "°", 1, "Stonyhurst longitude of the shock centre and apex."),
        ("lat_deg", "Lat", -89.0, 89.0, "°", 1, "Stonyhurst latitude of the shock centre and apex."),
        ("tilt_deg", "Tilt", -90.0, 90.0, "°", 1,
         "Ellipsoid only: rotation about the radial axis. At 0 the b axis is parallel to the equator."),
        ("height_rsun", "Height", 1.05, 30.0, " R☉", 2,
         "Heliocentric distance of the shock apex (centre distance + radial semi-axis a)."),
        ("kappa", "κ", 0.05, 2.0, "", 3,
         "Self-similar constant κ = b / (height − 1 R☉): the lateral size for a given apex height."),
        ("epsilon", "ε", -0.99, 0.99, "", 3,
         "Signed eccentricity: positive stretches the shock radially (a > b), negative flattens it (a < b)."),
        ("alpha", "α (b/c)", 0.5, 1.5, "", 3, "Ellipsoid only: ratio of the two lateral semi-axes, b / c."),
    )
    #: Parameters a spheroid does not have.
    ELLIPSOID_ONLY = ("tilt_deg", "alpha")

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._params: ShockParameters | None = None
        self._syncing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.shape_combo = QComboBox()
        for model in SHOCK_MODELS:
            self.shape_combo.addItem(SHOCK_MODEL_LABELS[model], model)
        self.shape_combo.setToolTip(
            "Spheroid: two equal lateral semi-axes, so no tilt — the simpler model and the\n"
            "safer choice with fewer than three views. Ellipsoid: adds the second lateral\n"
            "axis (α = b/c) and the tilt about the radial direction."
        )
        self.shape_combo.currentIndexChanged.connect(self._on_shape_changed)

        self.sliders: dict[str, GCSParameterSlider] = {}
        for name, label, low, high, unit, decimals, tip in self._SPECS:
            slider = GCSParameterSlider(label, low, minimum=low, maximum=high, unit=unit, decimals=decimals)
            slider.setToolTip(tip)
            slider.valueChanged.connect(lambda value, key=name: self._on_slider_changed(key, value))
            self.sliders[name] = slider
            layout.addWidget(slider)

        self.derived_label = QLabel("—")
        self.derived_label.setToolTip(
            "Centre distance and semi-axes implied by the parameters (PyThea's rcenter,\n"
            "radaxis a, orthoaxis1 b and orthoaxis2 c), in solar radii."
        )
        layout.addWidget(self.derived_label)

        self.refine_btn = QPushButton("Refine fit")
        self.refine_btn.setToolTip(
            "Least-squares polish of the shock against the front points you clicked: the\n"
            "model's projected outline is pulled through them in every view. A refinement,\n"
            "not a fit — align the shock by hand first. Errors are formal only; ε and tilt\n"
            "are weakly constrained with fewer than three views."
        )
        self.commit_btn = QPushButton("Commit Shock")
        self.commit_btn.setToolTip("Record this shock fit at the current time.")
        # The shape selector shares the button row: the panel must be no taller
        # than the GCS one it is stacked with, or the images give up height.
        buttons = QHBoxLayout()
        buttons.addWidget(self.shape_combo)
        buttons.addWidget(self.refine_btn)
        buttons.addWidget(self.commit_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        self.refine_btn.clicked.connect(self.refineRequested.emit)
        self.commit_btn.clicked.connect(self.commitRequested.emit)

    def parameters(self) -> ShockParameters:
        if self._params is None:
            values = {name: self.sliders[name].value() for name, *_ in self._SPECS}
            self._params = ShockParameters(model=self.shape_combo.currentData(), **values).as_model(
                self.shape_combo.currentData()
            )
        return self._params

    def show_parameters(self, params: ShockParameters) -> None:
        """Echo parameters into the controls without re-emitting."""
        self._params = params
        self._syncing = True
        try:
            index = self.shape_combo.findData(params.model)
            if index >= 0 and index != self.shape_combo.currentIndex():
                self.shape_combo.setCurrentIndex(index)
            for name, slider in self.sliders.items():
                slider.setValue(float(getattr(params, name)))
        finally:
            self._syncing = False
        self._sync_shape_controls(params.model)
        self._refresh_derived(params)

    def _on_shape_changed(self, _index: int) -> None:
        model = self.shape_combo.currentData()
        self._sync_shape_controls(model)
        if self._syncing:
            return
        self._params = self.parameters().as_model(model)
        self.show_parameters(self._params)
        self.parametersChanged.emit(self._params)

    def _on_slider_changed(self, name: str, value: float) -> None:
        if self._syncing:
            return
        self._params = self.parameters().replace_values(**{name: float(value)})
        self._refresh_derived(self._params)
        self.parametersChanged.emit(self._params)

    def _sync_shape_controls(self, model: str) -> None:
        # Hidden rather than disabled, as PyThea does: a spheroid has no such
        # parameters, and two idle rows would take height from the images.
        for name in self.ELLIPSOID_ONLY:
            self.sliders[name].setVisible(model == ELLIPSOID)

    def _refresh_derived(self, params: ShockParameters) -> None:
        if not params.is_physical:
            self.derived_label.setText("—")
            return
        axes = shock_semi_axes(params)
        lateral = (
            f"b {axes.orthoaxis1_rsun:.2f} · c {axes.orthoaxis2_rsun:.2f}"
            if params.model == ELLIPSOID
            else f"b = c {axes.orthoaxis1_rsun:.2f}"
        )
        # One short fixed line, for the reason GCSParameterPanel gives, and so the
        # panel stays no taller than the GCS one.
        self.derived_label.setText(f"centre {axes.rcenter_rsun:.2f} · a {axes.radaxis_rsun:.2f} · {lateral} R☉")


def default_shock(lon_deg: float, lat_deg: float = 0.0) -> ShockParameters:
    """The starting shock, aimed along the same direction as the starting GCS shell."""
    return ShockParameters(
        lon_deg=lon_deg,
        lat_deg=lat_deg,
        height_rsun=DEFAULT_SHOCK_HEIGHT_RSUN,
        kappa=DEFAULT_SHOCK_KAPPA,
        epsilon=0.0,
        model=SPHEROID,
    )
