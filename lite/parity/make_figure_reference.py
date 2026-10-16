"""Write matplotlib's own layout of the downloader figures, for Lite's tests.

Builds the FITS preview figure (``_build_plotutil_preview_figure`` with
``AppTheme.apply_mpl``'s colours) and the full-day spectral overview
(``render_spectral_overview_figure``) from the parity fixtures, draws them with
Agg at the canvas sizes the full app shows them in, and writes
``lite/parity/out/figures.json``: every axes rectangle, every text's window
extent and the renderer's text metrics, and the tick locations. The PNGs go
next to it for side-by-side checks.

usage (from the repository root, in the full app's environment):
    python lite/parity/make_figure_reference.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.text import Text  # noqa: E402

from src.backend.radio import spectral_overview as so  # noqa: E402
from src.backend.radio.fits_io import load_callisto_fits  # noqa: E402
from src.backend.radio.callisto_naming import parse_callisto_archive_filename  # noqa: E402
from src.ui.common.mpl_style import apply_origin_style  # noqa: E402
from src.ui.downloads.callisto_downloader import _build_plotutil_preview_figure  # noqa: E402

FIX = Path(__file__).with_name("fixtures")
OUT = Path(__file__).with_name("out")
COLORS = {"window": "#323232", "base": "#1e1e1e", "text": "#ffffff", "mid": "#a0a0a0"}


def apply_colors(fig, ax, cbar):
    """AppTheme.apply_mpl with fixed palette colours."""
    fig.patch.set_facecolor(COLORS["window"])
    ax.set_facecolor(COLORS["base"])
    ax.tick_params(colors=COLORS["text"], which="both")
    ax.xaxis.label.set_color(COLORS["text"])
    ax.yaxis.label.set_color(COLORS["text"])
    ax.title.set_color(COLORS["text"])
    for spine in ax.spines.values():
        spine.set_color(COLORS["text"])
    ax.grid(color=COLORS["mid"], alpha=0.25)
    cbar.ax.tick_params(colors=COLORS["text"], which="both")
    for t in cbar.ax.get_yticklabels():
        t.set_color(COLORS["text"])
    cbar.outline.set_edgecolor(COLORS["text"])
    cbar.ax.yaxis.label.set_color(COLORS["text"])


def dump(fig, name, width, height):
    fig.set_dpi(100)
    fig.set_size_inches(width / 100, height / 100)
    canvas = FigureCanvasAgg(fig)
    canvas.draw()
    renderer = canvas.get_renderer()
    H = fig.bbox.height

    def rect(b):
        return [b.x0, H - b.y1, b.x1, H - b.y0]

    texts, metrics = [], {}
    # Tick labels exist for every located tick; only the drawn ones count.
    tick_labels, drawn = set(), set()
    for ax in fig.axes:
        for axis in (ax.xaxis, ax.yaxis):
            for tick in axis.get_major_ticks() + axis.get_minor_ticks():
                tick_labels.update({id(tick.label1), id(tick.label2)})
            if not axis.get_visible():
                continue
            for tick in axis._update_ticks():
                for label in (tick.label1, tick.label2):
                    if label.get_visible():
                        drawn.add(id(label))
    for t in fig.findobj(Text):
        if not t.get_visible() or not t.get_text():
            continue
        if id(t) in tick_labels and id(t) not in drawn:
            continue
        bb = t.get_window_extent(renderer)
        if bb.width == 0:
            continue
        size = t.get_fontsize()
        texts.append({"text": t.get_text(), "size_pt": size, "rotation": t.get_rotation(), "bbox": rect(bb)})
        for s in (t.get_text(), "lp"):
            w, h, d = renderer.get_text_width_height_descent(s, t.get_fontproperties(), ismath=False)
            metrics[f"{s}|{size}"] = [w, h, d]
    axes = []
    for ax in fig.axes:
        axes.append({
            "rect": rect(ax.get_window_extent(renderer)),
            "xlim": list(ax.get_xlim()), "ylim": list(ax.get_ylim()),
            "xmajor": [float(v) for v in ax.xaxis.get_majorticklocs()],
            "ymajor": [float(v) for v in ax.yaxis.get_majorticklocs()],
            "xminor": [float(v) for v in ax.xaxis.get_minorticklocs()],
            "yminor": [float(v) for v in ax.yaxis.get_minorticklocs()],
        })
    canvas.print_png(str(OUT / f"figure.{name}.png"))
    return {"name": name, "width": width, "height": height, "axes": axes, "texts": texts, "metrics": metrics}


def main() -> int:
    apply_origin_style()
    OUT.mkdir(parents=True, exist_ok=True)
    cases = []
    for name, file in (("preview bir", "BIR_20261007_101459_01.fit.gz"),
                       ("preview gauri", "INDIA-GAURI_20261007_041500_02.fit.gz")):
        res = load_callisto_fits(str(FIX / file), memmap=False)
        fig, ax, cbar = _build_plotutil_preview_figure(res.data, res.freqs, res.time)
        apply_colors(fig, ax, cbar)
        case = dump(fig, name, 874, 480)
        case.update(kind="preview", file=file, colors=COLORS)
        cases.append(case)
    names = ["INDIA-GAURI_20261007_041500_02.fit.gz", "INDIA-GAURI_20261007_043000_02.fit.gz",
             "INDIA-GAURI_20261007_050000_02.fit.gz"]
    sources = []
    for n in names:
        station, observed, focus = parse_callisto_archive_filename(n)
        sources.append(so.SpectralOverviewSource(path=str(FIX / n), station=station, observed_at_utc=observed,
                                                 focus_code=focus, filename=n))
    result = so.build_spectral_overview(sources)
    fig = so.render_spectral_overview_figure(result)
    case = dump(fig, "overview gauri", 1452, 650)
    case.update(kind="overview", files=names)
    cases.append(case)
    (OUT / "figures.json").write_text(json.dumps(cases, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(cases)} figures written to {OUT / 'figures.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
