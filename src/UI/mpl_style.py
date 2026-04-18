"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations
from matplotlib import rcParams
from src.UI.font_utils import preferred_ui_font_family

_APPLIED = False

def apply_origin_style(force: bool = False) -> None:
    """Apply OriginPro-like rcParams once (safe to call many times)."""
    global _APPLIED
    if _APPLIED and not force:
        return

    preferred_family = preferred_ui_font_family()
    sans_families = []
    for family in (
        preferred_family,
        "DejaVu Sans",
        "Helvetica Neue",
        "Helvetica",
        "Arial",
        "Verdana",
        "Geneva",
        "sans-serif",
    ):
        name = str(family or "").strip()
        if name and name not in sans_families:
            sans_families.append(name)

    rcParams.update({
        "mathtext.fontset": "stix",
        "font.size": 12,
        "font.family": [preferred_family] if preferred_family else ["sans-serif"],
        "font.sans-serif": sans_families,
        #"font.weight": "bold",

        # Origin-like ticks
        "xtick.direction": "in",
        "ytick.direction": "in",
        "xtick.major.size": 6,
        "ytick.major.size": 6,
        "xtick.minor.size": 3,
        "ytick.minor.size": 3,
        "xtick.top": True,
        "ytick.right": True,

        # Borders and lines
        "axes.linewidth": 1.5,
        "lines.linewidth": 2,
    })

    # Optional params (only if supported by your Matplotlib)
    for k in ("xtick.minor.visible", "ytick.minor.visible"):
        if k in rcParams:
            rcParams[k] = True

    _APPLIED = True


def style_axes(ax, minor: bool = True) -> None:
    """Call once per Axes after you create it (adds minor ticks reliably)."""
    if ax is None:
        return
    if minor:
        try:
            ax.minorticks_on()
        except Exception:
            pass
    try:
        ax.tick_params(which="both", direction="in", top=True, right=True)
    except Exception:
        pass
