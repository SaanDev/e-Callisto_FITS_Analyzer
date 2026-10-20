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
    hidden = {id(t) for ax in fig.axes if not ax.get_visible() for t in ax.findobj(Text)}
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
        if id(t) in tick_labels and id(t) not in drawn or id(t) in hidden:
            continue
        bb = t.get_window_extent(renderer)
        if bb.width == 0:
            continue
        size = t.get_fontsize()
        texts.append({"text": t.get_text(), "size_pt": size, "rotation": t.get_rotation(), "bbox": rect(bb)})
        _, ismath = t._preprocess_math(t.get_text())
        for s, math in ((t.get_text(), ismath), ("lp", False)):
            w, h, d = renderer.get_text_width_height_descent(s, t.get_fontproperties(), ismath=math)
            metrics[f"{s}|{size}"] = [w, h, d]
    axes = []
    for ax in fig.axes:
        if not ax.get_visible():
            continue
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


def math_record(txt, prop, renderer):
    """A math label's layout as Agg's mathtext gives it (the glyphs `ship`
    places with the raster output's hinted metrics, the raster extents),
    and the glyph metrics, kerning and x-height it was computed from."""
    import matplotlib as mpl
    from matplotlib import _mathtext
    from matplotlib.backends.backend_agg import get_hinting_flag
    from matplotlib.mathtext import MathTextParser

    dpi = renderer.dpi
    fs = prop.get_size_in_points()
    fontset_class = MathTextParser._font_type_mapping[prop.get_math_fontfamily()]
    fontset = fontset_class(prop, get_hinting_flag())
    box = _mathtext.Parser().parse(txt, fontset, fs, dpi)
    glyphs = [[chr(info.num), info.fontsize, ox, oy] for ox, oy, info in _mathtext.ship(box).glyphs]
    chars = sorted(set(txt) | {"\N{MINUS SIGN}"})
    glyph_metrics, kerns = {}, {}
    for size in (fs, fs * _mathtext.SHRINK_FACTOR):
        for c in chars:
            try:
                m = fontset._get_info("default", "rm", c, size, dpi).metrics
            except Exception:
                continue
            glyph_metrics[f"{c}|{size * dpi / 72:.6f}"] = [m.advance, m.width, m.height, m.xmin, m.xmax, m.ymin, m.ymax, m.iceberg]
        for a in chars:
            for b in chars:
                k = fontset.get_kern("default", "rm", a, size, "default", "rm", b, size, dpi)
                if k:
                    kerns[f"{a}{b}|{size * dpi / 72:.6f}"] = k
    return {"text": txt, "size_px": fs * dpi / 72, "glyphs": glyphs, "box": [box.width, box.height, box.depth],
            "agg": list(renderer.get_text_width_height_descent(txt, prop, ismath=True)),
            "x_height": fontset.get_xheight("default", fs, dpi), "glyph_metrics": glyph_metrics, "kerns": kerns,
            "default_font_class": mpl.rcParams["mathtext.default"]}


def dump_analysis(fig, name, width, height):
    """`dump`, plus what the analysis figures add: titles (already texts),
    every Line2D and collection in display pixels, the legend's frame and
    handles, and the glyph layout of mathtext labels."""
    case = dump(fig, name, width, height)
    canvas = fig.canvas
    renderer = canvas.get_renderer()
    H = fig.bbox.height
    from matplotlib.mathtext import MathTextParser

    def xy(points):
        return [[float(x), float(H - y)] for x, y in points]

    axes_extra = []
    for ax in fig.axes:
        lines = []
        for line in ax.get_lines():
            pts = line.get_transform().transform(line.get_xydata())
            lines.append({"marker": str(line.get_marker()), "markersize": line.get_markersize(),
                          "linestyle": str(line.get_linestyle()), "linewidth": line.get_linewidth(),
                          "color": line.get_color(), "n": len(pts), "first": xy(pts[:1]), "last": xy(pts[-1:])})
        collections = []
        for coll in ax.collections:
            offs = coll.get_offset_transform().transform(coll.get_offsets())
            collections.append({"n": len(offs), "sizes": [float(v) for v in coll.get_sizes()[:1]],
                                "linewidths": [float(v) for v in coll.get_linewidths()[:1]],
                                "first": xy(offs[:1]), "last": xy(offs[-1:])})
        legend = None
        leg = ax.get_legend()
        if leg is not None:
            frame = leg.get_frame().get_window_extent(renderer)
            handles = []
            for h in leg.legend_handles:
                if hasattr(h, "get_xydata"):
                    pts = h.get_transform().transform(h.get_xydata())
                    handles.append({"kind": "line", "marker": str(h.get_marker()), "linestyle": str(h.get_linestyle()),
                                    "points": xy(pts)})
            math = []
            for t in leg.get_texts():
                txt = t.get_text()
                if "$" in txt:
                    math.append(math_record(txt, t.get_fontproperties(), renderer))
            legend = {"frame": [frame.x0, H - frame.y1, frame.x1, H - frame.y0], "loc": leg._loc, "handles": handles,
                      "math": math, "fontsize": leg._fontsize}
        axes_extra.append({"lines": lines, "collections": collections, "legend": legend,
                           "title": ax.get_title()})
    case["analysis"] = axes_extra
    return case


def analysis_figures():
    """The Maximum Intensities window's scatter and the Analyzer's graphs, at
    the canvas sizes the full app shows them (`capture_analysis.py`), from
    the maxima of a lasso-isolated burst as the windows get them."""
    import numpy as np
    from matplotlib.figure import Figure
    from matplotlib.path import Path as MplPath
    from scipy.optimize import curve_fit

    from src.backend.radio.frequency_axis import frequency_step_mhz, matplotlib_extent
    from src.backend.radio.radio_figures import draw_fit_graph, origin_graph_style, power_law_label
    from src.ui.common.mpl_style import style_axes

    name = "INDIA-GAURI_20261007_041500_02.fit.gz"
    res = load_callisto_fits(str(FIX / name), memmap=False)
    data = np.asarray(res.data, dtype=np.float32)
    data = data - np.nanmean(data, axis=1, keepdims=True).astype(np.float32)
    freqs, time = np.asarray(res.freqs, dtype=float), np.asarray(res.time, dtype=float)
    lasso = [(100.0, 85.0), (300.0, 88.0), (500.0, 70.0), (520.0, 45.0), (350.0, 35.0), (150.0, 50.0), (150.0, 50.0)]
    x0, x1, y0, y1 = (float(v) for v in matplotlib_extent(freqs, time, default_step=frequency_step_mhz(freqs, default=1.0)))
    ny, nx = data.shape
    xs = x0 + (np.arange(nx) + 0.5) * (x1 - x0) / nx
    ys = y1 + (np.arange(ny) + 0.5) * (y0 - y1) / ny
    X, Y = np.meshgrid(xs, ys)
    verts = np.vstack([np.asarray(lasso), lasso[0]])
    mask = MplPath(verts, closed=True).contains_points(np.column_stack((X.ravel(), Y.ravel()))).reshape(data.shape)
    iso = np.where(mask, data, 0.0)
    peak = np.argmax(np.where(np.isfinite(iso), iso, -np.inf), axis=0)
    t_sec, f_max = time.copy(), freqs[peak]
    vals = np.nanmax(iso, axis=0)
    positive = vals[np.isfinite(vals) & (vals > 0)]
    keep = np.isfinite(vals) & (vals > max(1e-12, float(np.nanpercentile(positive, 10)) * 0.15))
    t_sec, f_max = t_sec[keep], f_max[keep]
    stem = name.split(".")[0]
    cases = []

    def canvas_figure(w, h):
        fig = Figure(figsize=(w / 100, h / 100), dpi=100)
        FigureCanvasAgg(fig)
        return fig

    # The Maximum Intensities window.
    fig = canvas_figure(978, 564)
    ax = fig.add_subplot(111)
    style_axes(ax)
    ax.scatter(t_sec, f_max, marker="o", s=5, color="red")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Frequency (MHz)")
    ax.set_title("Maximum Intensity for Each Time Channel")
    case = dump_analysis(fig, "max scatter", 978, 564)
    case.update(kind="analysis", x=list(map(float, t_sec)), y=list(map(float, f_max)))
    cases.append(case)

    # The Analyzer: Maximum Intensities.
    fig = canvas_figure(772, 598)
    ax = fig.add_subplot(111)
    style_axes(ax)
    ax.clear()
    ax.scatter(t_sec, f_max, s=10, color="blue")
    ax.set_title(f"{stem}_Maximum_Intensity")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Frequency (MHz)")
    ax.grid(True)
    case = dump_analysis(fig, "analyzer max", 772, 598)
    case.update(kind="analysis", x=list(map(float, t_sec)), y=list(map(float, f_max)))
    cases.append(case)

    # The Analyzer: Best Fit, OriginPro-style.
    model = lambda x, a, b: a * x ** (-b)  # noqa: E731
    ok = (t_sec > 0) & (f_max > 0)
    lx, ly = np.log(t_sec[ok]), np.log(f_max[ok])
    slope, intercept = np.polyfit(lx, ly, 1)
    (a, b), _cov = curve_fit(model, t_sec[ok], f_max[ok], p0=(float(np.exp(intercept)), max(1e-9, -slope)),
                             bounds=([1e-12, 1e-9], [np.inf, np.inf]), maxfev=10000)
    fit_x = np.linspace(t_sec[ok].min(), t_sec[ok].max(), 400)
    fig = canvas_figure(772, 598)
    with origin_graph_style() as text:
        fig.set_facecolor("white")
        ax = fig.add_subplot(111)
        draw_fit_graph(ax, t_sec, f_max, fit_x=fit_x, fit_y=model(fit_x, a, b), data_label="Original Data",
                       fit_label=power_law_label(a, b, prefix="Best Fit", variable="x"), title=f"{stem}_Best_Fit",
                       x_label="Time (s)", y_label="Frequency (MHz)", text=text)
    case = dump_analysis(fig, "analyzer fit", 772, 598)
    case.update(kind="analysis", x=list(map(float, t_sec)), y=list(map(float, f_max)), a=float(a), b=float(b))
    cases.append(case)
    return cases


def batch_figures():
    """The PNG figures batch processing writes, captured from the full app's
    own `save_background_subtracted_png` (its `savefig` intercepted), for
    the modes and settings the worker passes."""
    import numpy as np
    from matplotlib.figure import Figure

    from src.backend.radio import batch_processing as bp
    from src.backend.radio.artemis import db_scale_for_header, linear_unit_for_header
    from src.backend.radio.fits_io import extract_ut_start_sec

    captured = []
    real_savefig = Figure.savefig
    current = {}

    def fake_savefig(self, *args, **kwargs):
        # The full app clears the figure after saving: take its layout now.
        captured.append(dump(self, current["name"], 1000, 600))

    cases = []
    runs = [
        ("batch mean", "INDIA-GAURI_20261007_041500_02.fit.gz", "background_subtracted", "mean", "Custom", None),
        ("batch raw", "BIR_20261007_101459_01.fit.gz", "raw", "mean", "viridis", None),
        ("batch plotutil", "GERMANY-DLR_20261007_050000_01.fit.gz", "background_subtracted", "plotutil_median_db", "inferno", None),
        ("batch locked", "INDIA-GAURI_20261007_041500_02.fit.gz", "background_subtracted", "median", "magma", {
            "version": 1, "range": {"time_start_s": 100.0, "time_stop_s": 400.0, "freq_min_mhz": 40.0, "freq_max_mhz": 80.0},
            "visual": {"use_db": False, "use_utc": False, "cmap": "plasma", "graph": {}}, "_include_visual": True}),
    ]
    Figure.savefig = fake_savefig
    try:
        for name, file, mode, method, cmap, cfg in runs:
            current["name"] = name
            res = load_callisto_fits(str(FIX / file), memmap=False)
            instrument = db_scale_for_header(res.header0, bp.DEFAULT_DB_SCALE)
            plotutil = db_scale_for_header(res.header0, bp.PLOTUTIL_DB_SCALE)
            if mode == "raw":
                data, suffix, units, scale, limits = np.asarray(res.data, dtype=np.float32), "Raw", "digits", instrument, None
            else:
                data = bp.subtract_background(res.data, method=method, db_scale=plotutil)
                suffix = f"Background Subtracted ({bp.background_method_label(method)})"
                units = "db" if method == bp.BACKGROUND_METHOD_PLOTUTIL else "digits"
                scale = plotutil if units == "db" else instrument
                limits = bp.PLOTUTIL_DISPLAY_LIMITS if method == bp.BACKGROUND_METHOD_PLOTUTIL else None
            stem = file[:-7] if file.endswith(".fit.gz") else file
            view = None
            if cfg is not None:
                view = bp.normalize_view_config(cfg)
                view["_include_visual"] = cfg.get("_include_visual", True)
            bp.save_background_subtracted_png(
                data, res.freqs, res.time, "unused.png", f"{stem}-{suffix}", cmap,
                ut_start_sec=extract_ut_start_sec(res.header0) or 0.0, cold_digits=0.0, db_scale=scale,
                data_units=units, default_display_limits=limits, view_config=view,
                linear_unit=linear_unit_for_header(res.header0),
            )
            case = captured.pop()
            case.update(kind="batch", file=file, mode=mode, method=method, cmap=cmap, view_config=cfg)
            cases.append(case)
    finally:
        Figure.savefig = real_savefig
    return cases


def comparison_figures():
    """The multi-station comparison's grid export
    (`render_comparison_grid_figure`), from the panels the window builds."""
    from src.backend.radio import multi_station_comparison as msc

    six = ["GERMANY-DLR_20261007_050000_01.fit.gz", "TRIEST_20261007_050000_57.fit.gz",
           "INDIA-GAURI_20261007_050000_02.fit.gz", "AUSTRIA-OE3FLB_20261007_051500_55.fit.gz",
           "GERMANY-DLR_20261007_050000_02.fit.gz", "TRIEST_20261007_050000_59.fit.gz"]
    runs = [
        ("comparison grid combined", six, "ut", False, "Custom", "shared", None, None),
        ("comparison grid per station", six[:4], "ut", True, "inferno", "per_station", 2, None),
        ("comparison grid locked", six[:4], "seconds", False, "viridis", "shared", 4,
         {"time_start_s": 100.0, "time_stop_s": 400.0, "freq_min_mhz": 40.0, "freq_max_mhz": 80.0}),
    ]
    cases = []
    for name, files, mode, use_db, cmap, scale, columns, display_range in runs:
        datasets = msc.combined_comparison_datasets_from_paths([str(FIX / f) for f in files])
        visual = {"use_db": use_db, "cmap": cmap}
        payloads, effective, _warnings = msc.comparison_panel_payloads(
            datasets, alignment_mode=mode, visual=visual, color_scale_mode=scale)
        labels = [p.dataset.label for p in payloads]
        dates = sorted({part for label in labels for part in label.split() if len(part) == 10 and part[4] == "-"})
        title = "e-CALLISTO Multi-Station Comparison" + (f" - {dates[0].replace('-', '/')}" if len(dates) == 1 else "")
        result = msc.render_comparison_grid_figure(
            payloads, alignment_mode=effective, display_range=display_range, visual=visual,
            color_scale_mode=scale, columns=columns, title=title)
        fig = result.figure
        w, h = fig.get_size_inches()
        case = dump(fig, name, round(w * 100), round(h * 100))
        case.update(kind="comparison grid", files=files, mode=mode, use_db=use_db, cmap=cmap, scale=scale,
                    columns=columns, display_range=display_range, title=title)
        cases.append(case)
    return cases


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
    cases.extend(analysis_figures())
    cases.extend(batch_figures())
    cases.extend(comparison_figures())
    (OUT / "figures.json").write_text(json.dumps(cases, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(cases)} figures written to {OUT / 'figures.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
