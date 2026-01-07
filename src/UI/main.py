"""
e-CALLISTO FITS Analyzer
Version 1.7.4
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

# main.py (TOP OF FILE, before anything else)
import os
import sys

if sys.platform.startswith("linux"):
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    os.environ.setdefault("QT_OPENGL", "software")
    os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")

    extra = (
        "--disable-gpu "
        "--disable-gpu-compositing "
        "--disable-features=VaapiVideoDecoder "
        "--disable-dev-shm-usage "
    )
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
        (os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "") + " " + extra).strip()
    )

import platform
from PySide6.QtCore import Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from src.UI.quick_app import AppController
import faulthandler

if getattr(sys, "frozen", False):
    base_path = os.path.abspath(
        os.path.join(os.path.dirname(sys.executable), "..", "Resources")
    )
else:
    base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

if base_path not in sys.path:
    sys.path.insert(0, base_path)


def resource_path(relative_path: str) -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(base_path, relative_path)
    return os.path.join(base_path, relative_path)


if sys.platform.startswith("linux"):
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    QApplication.setAttribute(Qt.AA_UseSoftwareOpenGL, True)

if platform.system() != "Windows":
    faulthandler.enable()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    QQuickStyle.setStyle("Material")

    controller = AppController()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("appController", controller)

    qml_path = resource_path(os.path.join("src", "UI", "qml", "Main.qml"))
    engine.load(QUrl.fromLocalFile(qml_path))

    if not engine.rootObjects():
        sys.exit(1)

    sys.exit(app.exec())
