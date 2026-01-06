"""
e-CALLISTO FITS Analyzer
Version 1.7.4
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

# main.py (TOP OF FILE, before anything else)
import os, sys

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
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.UI.gui_main import MainWindow
import faulthandler

if sys.platform.startswith("linux"):
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    QApplication.setAttribute(Qt.AA_UseSoftwareOpenGL, True)

#Uncomment when building with Windows
""""
app = QApplication(sys.argv)
if sys.platform.startswith("win"):
    app.setStyle("Fusion")
"""

if platform.system() != "Windows":
    faulthandler.enable()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.showMaximized()
    #window.show()
    sys.exit(app.exec())

