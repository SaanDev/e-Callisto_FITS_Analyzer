"""Measure where elements sit in the full app's screenshot vs Lite's.

For each probe region (logical px), find the bounding box of "ink" (pixels
that differ from the region's most common colour) in both images and print
the offsets. Positive dx means Lite is further right.

usage: python lite/tools/probe_offsets.py <reference.png> <lite.png> [scale]
"""

from __future__ import annotations

import sys
from collections import Counter

from PIL import Image

ref_path, lite_path = sys.argv[1], sys.argv[2]
S = float(sys.argv[3]) if len(sys.argv) > 3 else 1.5

PROBES = [
    # name, x0, y0, x1, y1 (logical, window coordinates)
    ("menu File", 13, 3, 51, 31),
    ("menu Edit", 57, 3, 98, 31),
    ("menu Help", 537, 3, 582, 31),
    ("tool Open icon", 8, 42, 57, 91),
    ("tool Export icon", 118, 42, 167, 91),
    ("toolbar sep 1", 278, 36, 294, 97),
    ("cite text", 1480, 55, 1585, 78),
    ("cite box", 1460, 38, 1600, 95),
    ("card1 arrow", 30, 132, 50, 152),
    ("card1 title", 55, 128, 160, 156),
    ("timeline status", 30, 170, 280, 199),
    ("Method label", 30, 276, 120, 291),
    ("combo text", 34, 302, 120, 329),
    ("combo chevron", 240, 302, 270, 329),
    ("subtract btn text", 60, 343, 250, 368),
    ("bg status", 30, 380, 280, 396),
    ("Lower label", 30, 473, 160, 499),
    ("value card text", 172, 474, 268, 498),
    ("slider handle", 135, 505, 168, 528),
    ("checkbox box", 30, 594, 50, 615),
    ("checkbox text", 55, 594, 260, 615),
    ("Intensity label", 30, 700, 120, 716),
    ("radio Digits dot", 30, 724, 52, 745),
    ("radio Digits text", 55, 724, 120, 745),
    ("radio dB ring", 30, 753, 52, 774),
    ("Freq scale label", 30, 860, 160, 876),
    ("toggle arrow", 300, 520, 316, 560),
    ("splitter grip", 292, 520, 297, 560),
    ("status text", 1380, 980, 1575, 999),
    ("size grip", 1583, 983, 1600, 1000),
]


def ink_box(im, box):
    x0, y0, x1, y1 = (int(round(v * S)) for v in box)
    crop = im.crop((x0, y0, x1, y1))
    bg = Counter(crop.getdata()).most_common(1)[0][0]
    w, h = crop.size
    xs, ys = [], []
    px = crop.load()
    for y in range(h):
        for x in range(w):
            p = px[x, y]
            if max(abs(p[i] - bg[i]) for i in range(3)) > 40:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return ((x0 + min(xs)) / S, (y0 + min(ys)) / S, (x0 + max(xs) + 1) / S, (y0 + max(ys) + 1) / S)


ref = Image.open(ref_path).convert("RGB")
lite = Image.open(lite_path).convert("RGB")
print(f"{'probe':<20} {'ref box (l,t,r,b)':<30} {'dx_left':>8} {'dx_right':>9} {'dy_top':>7} {'dy_bot':>7}")
for name, *box in PROBES:
    a, b = ink_box(ref, box), ink_box(lite, box)
    if a is None or b is None:
        print(f"{name:<20} ref={a} lite={b}")
        continue
    print(
        f"{name:<20} ({a[0]:6.1f},{a[1]:6.1f},{a[2]:6.1f},{a[3]:6.1f})  "
        f"{b[0]-a[0]:+8.2f} {b[2]-a[2]:+9.2f} {b[1]-a[1]:+7.2f} {b[3]-a[3]:+7.2f}"
    )
