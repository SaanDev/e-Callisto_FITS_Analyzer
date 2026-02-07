"""
e-CALLISTO FITS Analyzer
Version 1.7.7
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

import faulthandler
import os
import platform
import sys

def _project_base_path() -> str:
    # py2app executable lives in .../e-CALLISTO FITS Analyzer.app/Contents/MacOS
    if getattr(sys, "frozen", False):
        return os.path.abspath(os.path.join(os.path.dirname(sys.executable), "..", "Resources"))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _configure_platform_env() -> None:
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
        return

    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        app_root = os.path.abspath(os.path.join(os.path.dirname(sys.executable), ".."))
        frameworks_dir = os.path.join(app_root, "Frameworks")
        if os.path.isdir(frameworks_dir):
            current = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
            parts = [p for p in current.split(":") if p]
            if frameworks_dir not in parts:
                parts.insert(0, frameworks_dir)
                os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(parts)


BASE_PATH = _project_base_path()
if BASE_PATH not in sys.path:
    sys.path.insert(0, BASE_PATH)

_configure_platform_env()

# Now import from src (after sys.path is configured)
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from src.UI.mpl_style import apply_origin_style
from src.UI.gui_main import MainWindow
from src.UI.theme_manager import AppTheme


# Must be set before QApplication is created.
QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
if sys.platform.startswith("linux"):
    QApplication.setAttribute(Qt.AA_UseSoftwareOpenGL, True)


def _load_app_icon() -> QIcon:
    candidates = []

    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        candidates.extend(
            [
                os.path.join(exe_dir, "icon.ico"),
                os.path.join(getattr(sys, "_MEIPASS", ""), "icon.ico"),
                sys.executable,
            ]
        )
    else:
        candidates.extend(
            [
                os.path.join(BASE_PATH, "icon.ico"),
                os.path.join(BASE_PATH, "assets", "icon.ico"),
            ]
        )

    for path in candidates:
        if not path:
            continue
        if os.path.exists(path):
            icon = QIcon(path)
            if not icon.isNull():
                return icon

    return QIcon()


def main() -> int:
    if platform.system() != "Windows":
        faulthandler.enable()

    app = QApplication(sys.argv)
    if sys.platform.startswith("win"):
        app.setStyle("Fusion")
        app_icon = _load_app_icon()
        if not app_icon.isNull():
            app.setWindowIcon(app_icon)

    apply_origin_style()
    theme = AppTheme(app)
    app.setProperty("theme_manager", theme)

    window = MainWindow(theme=theme)
    if sys.platform.startswith("win"):
        app_icon = app.windowIcon()
        if not app_icon.isNull():
            window.setWindowIcon(app_icon)
    window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
