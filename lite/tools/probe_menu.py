"""Compare text positions inside a menu popup: full app vs Lite.

For each 30 px item band, finds the ink box of the label (left part) and of
the shortcut (right part) in both crops and prints the offsets (logical px).

usage: python lite/tools/probe_menu.py <reference_menu.png> <lite_menu.png> [split_x]
"""

import sys

from PIL import Image

S = 1.5
ref = Image.open(sys.argv[1]).convert("RGB")
lite = Image.open(sys.argv[2]).convert("RGB")
split = float(sys.argv[3]) if len(sys.argv) > 3 else None


def ink(im, x0, y0, x1, y1):
    xs, ys = [], []
    for y in range(int(y0 * S), int(y1 * S)):
        for x in range(int(x0 * S), min(int(x1 * S), im.width)):
            r, g, b = im.getpixel((x, y))[:3]
            bg = im.getpixel((int(x0 * S) + 2, int(y0 * S) + 2))[:3]
            if max(abs(r - bg[0]), abs(g - bg[1]), abs(b - bg[2])) > 60:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return min(xs) / S, min(ys) / S, (max(xs) + 1) / S, (max(ys) + 1) / S


w = ref.width / S
h = ref.height / S
y = 7.0
print(f"popup ref {w:.1f}x{h:.1f}  lite crop {lite.width / S:.1f}x{lite.height / S:.1f}")
split = split or w * 0.62
while y + 11 <= h - 7:
    a = ink(ref, 9, y + 2, w - 9, y + 9)
    if a is None and ink(ref, 9, y, w - 9, y + 11) is not None and y + 30 > h - 7:
        break
    band = (y, y + 30)
    left_r, left_l = ink(ref, 8, band[0] + 4, split, band[1] - 4), ink(lite, 8, band[0] + 4, split, band[1] - 4)
    right_r, right_l = ink(ref, split, band[0] + 4, w - 8, band[1] - 4), ink(lite, split, band[0] + 4, w - 8, band[1] - 4)
    if left_r is None:  # separator band
        y += 11
        continue
    out = f"y={y:5.1f} label ref x={left_r[0]:6.1f} y={left_r[1]:6.1f}"
    if left_l:
        out += f"  d=({left_l[0] - left_r[0]:+.2f},{left_l[1] - left_r[1]:+.2f})"
    if right_r and right_l:
        out += f"   right ref x={right_r[0]:6.1f}  d=({right_l[0] - right_r[0]:+.2f},{right_l[1] - right_r[1]:+.2f})"
    print(out)
    y += 30
