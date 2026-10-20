"""Reference images for Lite's port of Qt's aliased line drawing.

Draws random polylines with the cosmetic pens pyqtgraph's overlays use
(antialiasing off, as the full app's hardware canvas sets it) into
ARGB32-premultiplied images, the way ``PlotCurveItem.paint`` draws them:
``drawLines`` for wide, solid, opaque pens and ``drawPath`` for the rest.
Everything is in device pixels (no device pixel ratio).

Writes ``parity/out/raster/cases.json`` and one PNG per case, which
``crates/callisto-core/tests/raster_parity.rs`` replays.

usage: venv/Scripts/python.exe lite/parity/make_raster_reference.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from PySide6.QtCore import QLineF, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

OUT = Path(__file__).resolve().parent / "out" / "raster"
W, H = 160, 120
STYLES = {"solid": Qt.PenStyle.SolidLine, "dash": Qt.PenStyle.DashLine, "dot": Qt.PenStyle.DotLine}


def draw(case):
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    p.setClipRect(QRect(*case["clip"]))
    color = QColor(*case["rgba"])
    pen = QPen(color)
    pen.setWidthF(case["width"])
    pen.setCosmetic(True)
    pen.setStyle(STYLES[case["style"]])
    p.setPen(pen)
    pts = [QPointF(x, y) for x, y in case["points"]]
    # PlotCurveItem._shouldUseDrawLineSegments
    segmented = case["width"] > 1.0 and case["style"] == "solid" and case["rgba"][3] == 255
    if segmented:
        p.drawLines([QLineF(a, b) for a, b in zip(pts, pts[1:])])
    else:
        path = QPainterPath(pts[0])
        for q in pts[1:]:
            path.lineTo(q)
        p.drawPath(path)
    p.end()
    return img


def main():
    QGuiApplication(sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20261016)
    cases = []
    widths = [0.0, 0.5, 1.0, 1.2, 1.5, 2.0, 3.0, 5.0]
    for i in range(240):
        width = widths[i % len(widths)]
        style = ["solid", "solid", "dash", "dot"][(i // len(widths)) % 4]
        alpha = 255 if (i // 32) % 3 else 140
        n = rng.choice([2, 2, 3, 5, 12])
        pts = [(round(rng.uniform(-20, W + 20), 3), round(rng.uniform(-20, H + 20), 3)) for _ in range(n)]
        if i % 11 == 0:
            # Short steps, as a light curve's dense samples.
            x0, y0 = rng.uniform(10, 40), rng.uniform(20, 100)
            pts = [(round(x0 + k * 0.47, 3), round(y0 + rng.uniform(-3, 3), 3)) for k in range(160)]
        if i % 13 == 0 and n > 2:
            pts.append(pts[0])  # closed, as an annotation polygon
        if i % 17 == 0:
            pts = [(round(10.25 + k * 30.0, 3), 40.5 + (k % 2) * 0.0) for k in range(5)]  # axis aligned
        clip = [0, 0, W, H] if i % 5 else [12, 9, W - 30, H - 25]
        case = {"name": f"case{i:03d}", "points": pts, "width": width, "style": style,
                "rgba": [rng.randrange(256), rng.randrange(256), rng.randrange(256), alpha], "clip": clip}
        draw(case).save(str(OUT / f"{case['name']}.png"))
        cases.append(case)
    (OUT / "cases.json").write_text(json.dumps({"size": [W, H], "cases": cases}, indent=1), encoding="utf-8")
    print(f"{len(cases)} cases written to {OUT}")


if __name__ == "__main__":
    main()
