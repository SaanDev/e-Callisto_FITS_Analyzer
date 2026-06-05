"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


import os
import sys

if sys.platform.startswith("linux"):
    if (
        os.environ.get("CALLISTO_PREFER_QT_XCB", "").strip().lower() in {"1", "true", "yes", "on"}
        and not os.environ.get("QT_QPA_PLATFORM", "").strip()
        and os.environ.get("DISPLAY", "").strip()
        and (
            os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland"
            or os.environ.get("WAYLAND_DISPLAY", "").strip()
        )
    ):
        os.environ["QT_QPA_PLATFORM"] = "xcb;wayland"
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --disable-gpu-compositing")
    os.environ.setdefault("QT_OPENGL", "software")
    os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
