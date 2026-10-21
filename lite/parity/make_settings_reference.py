"""Write a QSettings INI file as the full app's settings would be on Linux,
with the values Lite's first-start import reads, and what they hold.

Lite's `settings_import` test parses ``settings/full_app.ini`` and compares
each value with ``settings/expected.json``. Nothing here touches the real
settings: the file is written with ``QSettings(path, IniFormat)``.

usage: python lite/parity/make_settings_reference.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
OUT = REPO / "lite" / "parity" / "settings"

from PySide6.QtCore import QSettings  # noqa: E402

from src.backend.session.view_config import build_display_range_preset, dump_display_range_presets_json  # noqa: E402

OUT.mkdir(parents=True, exist_ok=True)
ini = OUT / "full_app.ini"
if ini.exists():
    ini.unlink()
s = QSettings(str(ini), QSettings.IniFormat)
preset = build_display_range_preset("Burst, morning", {"time_start_s": 10, "time_stop_s": 200, "freq_min_mhz": 45, "freq_max_mhz": 80},
                                    time_mode="ut")
preset["created_at"] = "2026-10-01T10:00:00+05:30"
values = {
    "ui/theme_mode": "dark",
    "ui/view_mode": "modern",
    "ui/sidebar_sections_main": json.dumps({"Units": False, "Graph Properties": True, "Noise Clipping Thresholds": True}),
    "ui/frequency_axis_scale": "log",
    "processing/max_auto_clean_isolated": False,
    "processing/presets_json": json.dumps([{"name": "Wide = clip; é", "settings": {"cmap": "inferno"}}], indent=2),
    "processing/default_preset_name": "Wide = clip; é",
    "view/display_range_presets_json": dump_display_range_presets_json([preset]),
    "analysis/light_curve_settings_json": json.dumps({"mode": "multiple", "line_color": "#ff8800", "line_width": 2.5,
                                                      "show_frequency_label": False, "vertical_scale": 1.5, "opacity": 0.8,
                                                      "line_style": "dashed"}),
    "recent/fits": json.dumps([["C:\\data\\a b.fit", "C:\\data\\c,d.fit.gz"], ["/home/x/@e.fits"]]),
    "recent/projects": json.dumps(["C:\\data\\p.efaproj"]),
    "runtime/clean_exit": True,
}
for k, v in values.items():
    s.setValue(k, v)
s.sync()
expected = {k: (("true" if v else "false") if isinstance(v, bool) else v) for k, v in values.items()}
(OUT / "expected.json").write_text(json.dumps(expected, indent=1, ensure_ascii=False), encoding="utf-8")
print("wrote", ini.relative_to(REPO), "and expected.json")
