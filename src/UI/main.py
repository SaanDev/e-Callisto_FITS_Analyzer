"""
e-CALLISTO FITS Analyzer
Version 1.7.6
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

import os, sys
import platform
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from src.UI.theme_manager import AppTheme
from src.UI.mpl_style import apply_origin_style

apply_origin_style()

from src.UI.gui_main import MainWindow
import faulthandler

#linux
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


if getattr(sys, "frozen", False):
    base_path = os.path.abspath(
        os.path.join(os.path.dirname(sys.executable), "..", "Resources")
    )
else:
    base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

if base_path not in sys.path:
    sys.path.insert(0, base_path)


if sys.platform.startswith("linux"):
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    QApplication.setAttribute(Qt.AA_UseSoftwareOpenGL, True)

#app = QApplication(sys.argv)

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

    theme = AppTheme(app)
    app.setProperty("theme_manager", theme)

    window = MainWindow(theme=theme)
    window.showMaximized()
    #window.show()
    sys.exit(app.exec())

