from __future__ import annotations

import os
import sys

_QT_TEST_APP = None


def _ensure_qapplication():
    global _QT_TEST_APP
    if _QT_TEST_APP is not None:
        return _QT_TEST_APP

    # Headless Linux runners need an explicit platform plugin before Qt starts.
    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        return None

    _QT_TEST_APP = QApplication.instance() or QApplication([])
    _QT_TEST_APP.setQuitOnLastWindowClosed(False)
    return _QT_TEST_APP


_ensure_qapplication()
