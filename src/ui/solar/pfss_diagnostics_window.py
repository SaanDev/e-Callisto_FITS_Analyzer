"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Diagnostics for a PFSS solution.

The on-disk overlay answers "where do the field lines go"; this window answers
"should I believe them". Four panels, in the order a solution is worth checking:

  1. **Input magnetogram** -- the boundary condition, with its provenance. A PFSS
     solution is only ever as good as this, and the number that matters most is
     how far the synoptic map's date sits from the displayed frame.
  2. **Source-surface Br** with its polarity inversion lines. Where the model
     puts the heliospheric current sheet is its most directly checkable
     prediction: it can be compared against in-situ sector-boundary crossings.
  3. **Open/closed field map** in Carrington projection -- the same footpoint
     polarity that drives the on-disk region outlines, laid flat so the open-field
     regions can be compared against a synoptic EUV map's coronal holes.
  4. **Solution statistics** -- open flux, open-area fraction, grid, timings.

``MplCanvas`` (``src.ui.widgets.matplotlib_widget``) is deliberately not reused:
it hard-codes a single ``add_subplot(111)``, and panels 2 and 3 need axes built
with ``projection=<map>`` so astropy draws the Carrington WCS. The figure is
therefore assembled directly, then restyled for the active theme.
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.common.gui_shared import fit_window_to_screen, pick_export_path

WINDOW_TITLE = "PFSS Diagnostics"

#: Diverging map for signed radial field, the convention for magnetograms.
BR_CMAP = "RdBu_r"


class PfssDiagnosticsWindow(QMainWindow):
    """Four-panel audit of one PFSS solution."""

    def __init__(
        self,
        parent=None,
        *,
        solution: Any = None,
        traced: Any = None,
        provenance: dict[str, Any] | None = None,
        theme: Any = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.theme = theme
        self._solution = solution
        self._traced = traced
        self._provenance = dict(provenance or {})

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.figure = Figure(figsize=(11, 8), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas, 1)

        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        self.caption_label = QLabel("")
        self.caption_label.setObjectName("SolarHintLabel")
        self.caption_label.setWordWrap(True)
        self.save_btn = QPushButton("Save Figure…")
        self.save_btn.setToolTip("Save all four panels as a single image.")
        self.save_btn.clicked.connect(self._on_save)
        footer_layout.addWidget(self.caption_label, 1)
        footer_layout.addWidget(self.save_btn, 0)
        layout.addWidget(footer, 0)

        fit_window_to_screen(self, 1180, 860)
        self.refresh()

    # ------------------------------------------------------------------ draw
    def refresh(self) -> None:
        """Rebuild every panel from the current solution."""
        self.figure.clear()
        if self._solution is None:
            axes = self.figure.add_subplot(111)
            axes.axis("off")
            axes.text(0.5, 0.5, "No PFSS solution.", ha="center", va="center")
            self.canvas.draw_idle()
            return

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._draw_magnetogram()
            self._draw_source_surface()
            self._draw_open_closed()
            self._draw_stats()

        self.caption_label.setText(self._caption())
        try:
            self.figure.tight_layout()
        except Exception:
            pass
        self._apply_theme()
        self.canvas.draw_idle()

    def _draw_magnetogram(self) -> None:
        """Panel 1: the boundary condition actually used."""
        magnetogram = getattr(self._solution, "output", None)
        magnetogram = getattr(magnetogram, "input_map", None)
        axes = self.figure.add_subplot(2, 2, 1)
        if magnetogram is None:
            axes.axis("off")
            axes.set_title("Input magnetogram (unavailable)", fontsize=10)
            return

        data = np.asarray(magnetogram.data, dtype=float)
        limit = float(np.nanpercentile(np.abs(data), 99.5)) or 1.0
        image = axes.imshow(
            data, origin="lower", cmap=BR_CMAP, vmin=-limit, vmax=limit,
            extent=(0, 360, -90, 90), aspect="auto",
        )
        axes.set_title("1 · Input magnetogram (Br)", fontsize=10)
        axes.set_xlabel("Carrington longitude (deg)")
        axes.set_ylabel("Latitude (deg)")
        bar = self.figure.colorbar(image, ax=axes, fraction=0.046, pad=0.04)
        bar.set_label(self._provenance.get("bunit") or "Br")
        self._magnetogram_axes = (axes, bar)

        gaps = int(self._provenance.get("non_finite_pixels") or 0)
        if gaps:
            # Zero-filled polar gaps are a real limitation of the boundary
            # condition, not a rendering artefact, so they are labelled. Folded
            # into the axis label rather than floated below the axes, where it
            # would land on top of the panel underneath.
            percent = 100.0 * float(self._provenance.get("non_finite_fraction") or 0.0)
            axes.set_xlabel(
                f"Carrington longitude (deg)\n{gaps} unmeasured pixels zero-filled ({percent:.1f}%)",
                fontsize=8,
            )

    def _draw_source_surface(self) -> None:
        """Panel 2: Br at the source surface, plus the polarity inversion lines."""
        from src.backend.solar.pfss_model import source_surface_products

        axes = self.figure.add_subplot(2, 2, 2)
        try:
            ss_br, pils = source_surface_products(self._solution)
        except Exception:
            axes.axis("off")
            axes.set_title("Source surface (unavailable)", fontsize=10)
            return

        data = np.asarray(ss_br.data, dtype=float)
        limit = float(np.nanpercentile(np.abs(data), 99.5)) or 1.0
        image = axes.imshow(
            data, origin="lower", cmap=BR_CMAP, vmin=-limit, vmax=limit,
            extent=(0, 360, -90, 90), aspect="auto",
        )
        rss = float(getattr(getattr(self._solution, "params", None), "rss", 2.5))
        axes.set_title(f"2 · Source surface Br at {rss:g} R$_\\odot$", fontsize=10)
        axes.set_xlabel("Carrington longitude (deg)")
        axes.set_ylabel("Latitude (deg)")
        self.figure.colorbar(image, ax=axes, fraction=0.046, pad=0.04)

        # The Br = 0 contour is the heliospheric current sheet leaving the model.
        drawn = 0
        for pil in pils or []:
            lon, lat = self._pil_lonlat(pil)
            if lon is None:
                continue
            axes.plot(lon, lat, color="black", linewidth=1.2, linestyle="-")
            drawn += 1
        if drawn:
            axes.plot([], [], color="black", linewidth=1.2, label="Neutral line (HCS)")
            axes.legend(fontsize=7, loc="lower right")

    @staticmethod
    def _pil_lonlat(pil: Any) -> tuple[np.ndarray | None, np.ndarray | None]:
        """Longitude/latitude arrays of one polarity inversion line."""
        try:
            import astropy.units as u

            lon = np.atleast_1d(np.asarray(pil.lon.to_value(u.deg), dtype=float))
            lat = np.atleast_1d(np.asarray(pil.lat.to_value(u.deg), dtype=float))
        except Exception:
            return None, None
        if lon.size < 2:
            return None, None
        # Break the line where it wraps the 0/360 seam, or matplotlib draws a
        # spurious horizontal stroke straight across the map.
        wrap = np.nonzero(np.abs(np.diff(lon)) > 180.0)[0]
        if wrap.size:
            lon = lon.astype(float).copy()
            lat = lat.astype(float).copy()
            lon = np.insert(lon, wrap + 1, np.nan)
            lat = np.insert(lat, wrap + 1, np.nan)
        return lon, lat

    def _draw_open_closed(self) -> None:
        """Panel 3: footpoint polarity, flattened into Carrington coordinates."""
        from matplotlib.colors import ListedColormap

        axes = self.figure.add_subplot(2, 2, 3)
        grid = getattr(self._traced, "polarity_grid", None)
        if grid is None:
            axes.axis("off")
            axes.set_title("Open/closed map (not computed)", fontsize=10)
            return

        values = np.asarray(grid, dtype=float)
        # -1 inward-open, 0 closed, +1 outward-open: three flat colours rather
        # than a continuous map, because the quantity is categorical.
        cmap = ListedColormap(["#5f9bff", "#2b2b2b", "#ff5f5f"])
        axes.imshow(
            values, origin="lower", cmap=cmap, vmin=-1.5, vmax=1.5,
            extent=(0, 360, -90, 90), aspect="auto", interpolation="nearest",
        )
        axes.set_title("3 · Open (red/blue) and closed (dark) field", fontsize=10)
        axes.set_xlabel("Carrington longitude (deg)")
        axes.set_ylabel("Latitude (deg)")

        fraction = float(np.mean(np.abs(values) > 0.5))
        axes.text(
            0.02, 0.04, f"open area {100.0 * fraction:.1f}%",
            transform=axes.transAxes, fontsize=7, color="white",
        )

    def _draw_stats(self) -> None:
        """Panel 4: the numbers worth quoting."""
        from src.backend.solar.pfss_model import solution_stats

        axes = self.figure.add_subplot(2, 2, 4)
        axes.axis("off")
        axes.set_title("4 · Solution", fontsize=10)

        try:
            stats = solution_stats(self._solution, self._traced)
        except Exception:
            stats = {}

        rows = self._stat_rows(stats)
        text = "\n".join(f"{label:<26}{value}" for label, value in rows)
        axes.text(
            0.0, 0.97, text, transform=axes.transAxes, va="top", ha="left",
            fontsize=8, family="monospace",
        )

    def _stat_rows(self, stats: dict[str, Any]) -> list[tuple[str, str]]:
        """Label/value pairs for the statistics panel."""
        provenance = self._provenance
        rows: list[tuple[str, str]] = [
            ("Magnetogram", str(provenance.get("source_label") or "-")),
            ("Carrington rotation", str(provenance.get("carrington_rotation") or "-")),
            ("Magnetogram date", str(provenance.get("obstime") or "-").replace("T", " ")[:19]),
        ]
        offset = provenance.get("frame_offset_hours")
        if isinstance(offset, (int, float)) and np.isfinite(offset):
            days = abs(float(offset)) / 24.0
            rows.append((
                "Offset from frame",
                f"{days:.2f} d" if days >= 1 else f"{abs(float(offset)):.1f} h",
            ))
        if provenance.get("realizations"):
            rows.append((
                "ADAPT realization",
                f"{provenance.get('realization')} of {provenance.get('realizations')}",
            ))

        rows.append(("", ""))
        rows.append(("Source surface", f"{stats.get('rss', '-')} R_sun"))
        rows.append(("Radial cells", str(stats.get("nrho", "-"))))
        if stats.get("grid"):
            rows.append(("Grid (phi x s x r)", str(stats["grid"])))

        rows.append(("", ""))
        if stats.get("line_count") is not None:
            rows.append(("Field lines traced", str(stats["line_count"])))
            rows.append(("  open", str(stats.get("open_line_count", "-"))))
            rows.append(("  closed", str(stats.get("closed_line_count", "-"))))
        if stats.get("open_area_fraction") is not None:
            rows.append(("Open area fraction", f"{float(stats['open_area_fraction']):.3f}"))
        if stats.get("open_flux_mx") is not None:
            rows.append(("Open flux", f"{float(stats['open_flux_mx']):.3e} Mx"))
        if stats.get("solve_seconds") is not None:
            rows.append(("Solve time", f"{float(stats['solve_seconds']):.2f} s"))

        net = provenance.get("net_flux_ratio")
        if isinstance(net, (int, float)) and np.isfinite(net):
            # Reported, not corrected: pfss() excludes the monopole term itself.
            rows.append(("", ""))
            rows.append(("Residual net flux", f"{float(net):.4f} of peak |Br|"))
        return rows

    def _caption(self) -> str:
        from src.backend.solar.pfss_magnetograms import describe_magnetogram

        return (
            f"{describe_magnetogram(self._provenance)}  —  a synoptic map spans a whole "
            "Carrington rotation, so this describes the global field around the "
            "observation, not the instantaneous field."
        )

    def _apply_theme(self) -> None:
        """Restyle for the active light/dark theme, as the solar canvases do."""
        theme = self.theme
        if theme is None or not hasattr(theme, "apply_mpl"):
            return
        for axes in self.figure.get_axes():
            try:
                theme.apply_mpl(self.figure, axes)
            except Exception:
                pass

    # ------------------------------------------------------------------ save
    def _on_save(self) -> None:
        from src.backend.common.figure_export import save_figure

        path, _ext = pick_export_path(
            self, "Save PFSS diagnostics", "pfss_diagnostics.png",
            "PNG image (*.png);;PDF document (*.pdf);;SVG image (*.svg)",
        )
        if not path:
            return
        try:
            save_figure(self.figure, path)
        except Exception as exc:
            self.caption_label.setText(f"Could not save the figure: {exc}")
            return
        self.caption_label.setText(f"Saved {path}")

    # ----------------------------------------------------------------- update
    def set_solution(
        self,
        solution: Any,
        traced: Any = None,
        provenance: dict[str, Any] | None = None,
    ) -> None:
        """Point the window at a new solution and redraw."""
        self._solution = solution
        self._traced = traced
        if provenance is not None:
            self._provenance = dict(provenance)
        self.refresh()
