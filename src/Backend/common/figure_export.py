"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Publication export of matplotlib figures (src/backend/common/figure_export.py).

Exported graphs follow the OriginPro look — Arial, a closed black frame, inward
major and minor ticks on all four sides, filled symbols in Origin's default
colour order, a red fitted curve and a boxed legend — and are always drawn in
light mode, whatever theme the application is showing. On-screen plots keep the
application's own styling: this module only ever applies its settings inside
:func:`origin_style`, so nothing leaks into the interactive views.

Every figure export in the application offers the same formats, listed in
:data:`FIGURE_EXPORT_FILTERS`.
"""

from __future__ import annotations

import io
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

#: File dialog filters shared by every figure export in the application.
FIGURE_EXPORT_FILTERS = (
    "PNG (*.png);;PDF (*.pdf);;EPS (*.eps);;SVG (*.svg);;TIFF (*.tiff *.tif);;JPG (*.jpg *.jpeg)"
)

#: Suffix -> matplotlib format name, for every suffix the filters offer.
_FORMATS = {
    ".png": "png",
    ".pdf": "pdf",
    ".eps": "eps",
    ".svg": "svg",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
}

#: Resolution for raster formats; vector formats embed images at their own size.
EXPORT_DPI = 300

#: Origin's default colour increment list: black, red, blue, green, magenta,
#: dark cyan, olive, navy, purple, wine.
ORIGIN_COLORS = (
    "#000000",
    "#FF0000",
    "#0000FF",
    "#008000",
    "#FF00FF",
    "#008080",
    "#808000",
    "#000080",
    "#800080",
    "#800000",
)

#: Origin's default symbol increment: square, circle, up and down triangle, diamond.
ORIGIN_MARKERS = ("s", "o", "^", "v", "D", "<", ">", "p")

#: The fitted curve Origin draws over the data.
ORIGIN_FIT_COLOR = "#FF0000"


@lru_cache(maxsize=1)
def origin_font_families() -> tuple[str, ...]:
    """Arial first, as Origin uses, then metric-compatible stand-ins.

    DejaVu Sans ships with matplotlib and always closes the list. matplotlib
    falls back through the list glyph by glyph, which is what draws the ☉ of
    "R☉" and the ₀ of "v₀" (both absent from Arial) without a mathtext font
    change mid-label. Helvetica is deliberately not a stand-in: macOS's copy
    answers for those code points with blank glyphs, which stops the fallback.
    """
    from matplotlib import font_manager

    available = {font.name for font in font_manager.fontManager.ttflist}
    preferred = [name for name in ("Arial", "Liberation Sans", "Arimo") if name in available]
    return tuple(preferred + ["DejaVu Sans"])


def origin_rc() -> dict[str, Any]:
    """The rcParams of an OriginPro-style graph on a white page."""
    from cycler import cycler

    black = "#000000"
    return {
        # Light mode, explicitly: nothing may inherit a dark application theme.
        "figure.facecolor": "white",
        "figure.edgecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
        "savefig.edgecolor": "white",
        "savefig.transparent": False,
        "text.color": black,
        "axes.labelcolor": black,
        "axes.edgecolor": black,
        "axes.titlecolor": black,
        "xtick.color": black,
        "ytick.color": black,
        "xtick.labelcolor": black,
        "ytick.labelcolor": black,
        "grid.color": "#b0b0b0",
        # Type.
        "font.family": list(origin_font_families()),
        "font.size": 11,
        "axes.titlesize": 11,
        "axes.labelsize": 12,
        "xtick.labelsize": 11,
        "ytick.labelsize": 11,
        "legend.fontsize": 10,
        "axes.unicode_minus": True,
        # A closed frame with inward major and minor ticks on every side.
        "axes.linewidth": 1.5,
        "axes.spines.top": True,
        "axes.spines.right": True,
        "axes.grid": False,
        "xtick.direction": "in",
        "ytick.direction": "in",
        "xtick.top": True,
        "ytick.right": True,
        "xtick.bottom": True,
        "ytick.left": True,
        "xtick.minor.visible": True,
        "ytick.minor.visible": True,
        "xtick.major.size": 6,
        "ytick.major.size": 6,
        "xtick.minor.size": 3,
        "ytick.minor.size": 3,
        "xtick.major.width": 1.5,
        "ytick.major.width": 1.5,
        "xtick.minor.width": 1.0,
        "ytick.minor.width": 1.0,
        "xtick.major.pad": 5,
        "ytick.major.pad": 5,
        # Data and legend.
        "axes.prop_cycle": cycler(color=list(ORIGIN_COLORS)),
        "lines.linewidth": 1.5,
        "lines.markersize": 7,
        "errorbar.capsize": 3,
        "legend.frameon": True,
        "legend.fancybox": False,
        "legend.shadow": False,
        "legend.framealpha": 1.0,
        "legend.edgecolor": black,
        "legend.facecolor": "white",
        "legend.borderpad": 0.5,
        # Editable, embedded text in the vector formats.
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "path",
    }


@contextmanager
def origin_style() -> Iterator[None]:
    """Apply :func:`origin_rc` for the figures built inside the block only."""
    import matplotlib

    with matplotlib.rc_context(origin_rc()):
        yield


def style_origin_axes(ax: Any) -> None:
    """Origin's frame and ticks on one axes, whatever defaults it was built with."""
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(1.5)
        spine.set_color("#000000")
    ax.minorticks_on()
    ax.tick_params(which="both", direction="in", top=True, right=True, bottom=True, left=True)
    ax.tick_params(which="major", length=6, width=1.5)
    ax.tick_params(which="minor", length=3, width=1.0)


def origin_legend(ax: Any, **kwargs: Any) -> Any:
    """A square-cornered legend with a black border, as Origin draws it."""
    options = dict(frameon=True, fancybox=False, framealpha=1.0, edgecolor="#000000")
    options.update(kwargs)
    legend = ax.legend(**options)
    if legend is not None:
        legend.get_frame().set_linewidth(1.0)
    return legend


def export_format(path: str | Path) -> str:
    """The matplotlib format for ``path``'s suffix; raises for an unsupported one."""
    suffix = Path(path).suffix.lower()
    try:
        return _FORMATS[suffix]
    except KeyError:
        supported = ", ".join(sorted({key.lstrip(".") for key in _FORMATS}))
        raise ValueError(f"Unsupported figure format '{suffix or '(none)'}'. Use one of: {supported}.") from None


def save_figure(fig: Any, path: str | Path, *, dpi: int = EXPORT_DPI) -> Path:
    """Write ``fig`` in the format its suffix names, on a white page."""
    target = Path(path).expanduser()
    fmt = export_format(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {"format": fmt, "dpi": dpi, "facecolor": "white", "edgecolor": "white"}
    if fmt == "jpeg":
        options["pil_kwargs"] = {"quality": 95}
    elif fmt == "tiff":
        options["pil_kwargs"] = {"compression": "tiff_lzw"}
    _ensure_canvas(fig)
    fig.savefig(str(target), **options)
    return target


def figure_png_bytes(fig: Any, *, dpi: int = 200) -> bytes:
    """``fig`` as PNG bytes, for embedding in a PDF report."""
    buffer = io.BytesIO()
    _ensure_canvas(fig)
    fig.savefig(buffer, format="png", dpi=dpi, facecolor="white", edgecolor="white")
    return buffer.getvalue()


def figure_rgb(fig: Any) -> Any:
    """``fig`` rendered at its own size and dpi, as an ``(H, W, 3)`` uint8 array."""
    import numpy as np

    canvas = _ensure_canvas(fig)
    canvas.draw()
    return np.asarray(canvas.buffer_rgba())[..., :3].copy()


def _ensure_canvas(fig: Any) -> Any:
    """Attach an Agg canvas to a bare ``Figure`` (never pyplot: no GUI, no global state)."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    canvas = getattr(fig, "canvas", None)
    if not isinstance(canvas, FigureCanvasAgg):
        canvas = FigureCanvasAgg(fig)
    return canvas
