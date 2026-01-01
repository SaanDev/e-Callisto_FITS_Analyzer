"""
e-CALLISTO FITS Analyzer
Version 1.7.4
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

import platform
import os
import sys

# Must be set BEFORE importing PySide6 / QtWebEngine
if sys.platform.startswith("linux"):
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

    existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    extra = (
        "--disable-gpu "
        "--disable-gpu-compositing "
        "--disable-features=VaapiVideoDecoder,VaapiVideoEncoder "
        "--disable-accelerated-video-decode "
        "--disable-dev-shm-usage "
        "--no-sandbox"
    )
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (existing + " " + extra).strip()

    # Helps on older Intel chips when iHD fails
    os.environ.setdefault("LIBVA_DRIVER_NAME", "i965")

    # Safer fallback for OpenGL issues
    os.environ.setdefault("QT_OPENGL", "software")


from PySide6.QtWidgets import QApplication
from gui_main import MainWindow
import faulthandler

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


