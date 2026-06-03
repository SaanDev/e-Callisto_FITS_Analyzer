"""
e-CALLISTO FITS Analyzer
Version 2.6.0-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


import os
import sys

if sys.platform.startswith("linux"):
    current_qpa = os.environ.get("QT_QPA_PLATFORM", "").strip().lower()
    current_platforms = [part.strip() for part in current_qpa.split(";") if part.strip()]
    if (
        os.environ.get("CALLISTO_ALLOW_QT_WAYLAND", "").strip().lower() not in {"1", "true", "yes", "on"}
        and os.environ.get("DISPLAY", "").strip()
        and (
            os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland"
            or os.environ.get("WAYLAND_DISPLAY", "").strip()
        )
        and (
            not current_platforms
            or (
                any(part.startswith("wayland") for part in current_platforms)
                and not any(part.startswith("xcb") for part in current_platforms)
            )
        )
    ):
        os.environ["QT_QPA_PLATFORM"] = "xcb;wayland"
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --disable-gpu-compositing")
    os.environ.setdefault("QT_OPENGL", "software")
    os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
