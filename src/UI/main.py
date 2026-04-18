"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


import argparse
import faulthandler
import os
import platform
import sys
import threading

from PySide6.QtCore import QTimer


def _force_software_opengl() -> bool:
    raw = os.environ.get("CALLISTO_FORCE_SOFTWARE_OPENGL", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


FORCE_SOFTWARE_OPENGL = _force_software_opengl()


def _suppress_macos_tsm_warnings_enabled() -> bool:
    raw = os.environ.get("CALLISTO_SUPPRESS_MACOS_TSM_WARNINGS", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _install_macos_stderr_filter() -> None:
    if sys.platform != "darwin" or not _suppress_macos_tsm_warnings_enabled():
        return

    patterns = (
        b"TSMSendMessageToUIServer",
        b"com.apple.tsm.uiserver",
    )
    try:
        original_stderr_fd = os.dup(2)
        read_fd, write_fd = os.pipe()
        os.dup2(write_fd, 2)
        os.close(write_fd)
    except Exception:
        return

    def _pump() -> None:
        pending = b""
        try:
            while True:
                chunk = os.read(read_fd, 4096)
                if not chunk:
                    break
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    line_out = line + b"\n"
                    if any(pattern in line_out for pattern in patterns):
                        continue
                    os.write(original_stderr_fd, line_out)
            if pending and not any(pattern in pending for pattern in patterns):
                os.write(original_stderr_fd, pending)
        except Exception:
            try:
                if pending:
                    os.write(original_stderr_fd, pending)
            except Exception:
                pass
        finally:
            try:
                os.close(read_fd)
            except Exception:
                pass
            try:
                os.close(original_stderr_fd)
            except Exception:
                pass

    thread = threading.Thread(target=_pump, name="macos-stderr-filter", daemon=True)
    thread.start()


def _is_cme_helper_mode_argv(argv: list[str]) -> bool:
    tokens = [str(item or "").strip() for item in list(argv or [])]
    for idx, token in enumerate(tokens):
        if token.startswith("--mode="):
            return token.split("=", 1)[1].strip() == "cme-helper"
        if token == "--mode" and idx + 1 < len(tokens):
            return tokens[idx + 1].strip() == "cme-helper"
    return False


def _project_base_path() -> str:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            return os.path.realpath(os.path.abspath(meipass))

        exe_dir = os.path.realpath(os.path.abspath(os.path.dirname(sys.executable)))
        if sys.platform == "darwin":
            return os.path.realpath(os.path.abspath(os.path.join(exe_dir, "..", "Resources")))
        return exe_dir

    return os.path.realpath(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


def _configure_platform_env() -> None:
    if sys.platform.startswith("linux"):
        helper_mode = _is_cme_helper_mode_argv(sys.argv)
        if FORCE_SOFTWARE_OPENGL:
            os.environ.setdefault("QT_OPENGL", "software")
            os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
        if helper_mode:
            os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
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
_install_macos_stderr_filter()

# Now import from src (after sys.path is configured)
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from src.UI.font_utils import sanitize_application_font
from src.UI.runtime_paths import find_startup_logo_path
from src.version import APP_NAME, APP_VERSION


# Must be set before QApplication is created.
QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
if sys.platform.startswith("linux") and FORCE_SOFTWARE_OPENGL:
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


def _find_startup_logo_path() -> str:
    return find_startup_logo_path(base_path=BASE_PATH)


def _parse_cli_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--mode", choices=["main", "cme-helper", "goes-overlay-helper"], default="main")
    parser.add_argument("--movie-url", default="")
    parser.add_argument("--movie-title", default="")
    parser.add_argument("--movie-direct-url", default="")
    parser.add_argument("--ipc-name", default="")
    parser.add_argument("--request-file", default="")
    parser.add_argument("--response-file", default="")
    return parser.parse_known_args(argv[1:])


def _run_main_mode(app: QApplication) -> int:
    if sys.platform.startswith("win"):
        app.setStyle("Fusion")
        app_icon = _load_app_icon()
        if not app_icon.isNull():
            app.setWindowIcon(app_icon)

    from src.UI.startup_loading import StartupLoadingScreen

    startup_loading = StartupLoadingScreen(
        app_name=APP_NAME,
        version=APP_VERSION,
        logo_path=_find_startup_logo_path(),
    )
    if not app.windowIcon().isNull():
        startup_loading.setWindowIcon(app.windowIcon())
    startup_loading.present()
    startup_loading.set_progress(15, "Loading analysis modules...")

    from src.UI.main_window import MainWindow
    from src.UI.mpl_style import apply_origin_style
    from src.UI.theme_manager import AppTheme

    startup_loading.set_progress(45, "Applying interface styling...")
    apply_origin_style()
    theme = AppTheme(app)
    app.setProperty("theme_manager", theme)

    startup_loading.set_progress(80, "Preparing the main workspace...")
    window = MainWindow(theme=theme)
    if sys.platform.startswith("win"):
        app_icon = app.windowIcon()
        if not app_icon.isNull():
            window.setWindowIcon(app_icon)
    startup_loading.set_progress(100, "Opening analyzer...")
    window.showMaximized()
    app.processEvents()
    startup_loading.close()
    startup_loading.deleteLater()
    QTimer.singleShot(0, window.check_for_startup_updates)
    return app.exec()


def _run_cme_helper_mode(
    app: QApplication,
    movie_url: str,
    movie_title: str,
    movie_direct_url: str,
    ipc_name: str,
) -> int:
    from src.UI.cme_movie_helper import launch_cme_movie_helper
    from src.UI.mpl_style import apply_origin_style
    from src.UI.theme_manager import AppTheme

    if sys.platform.startswith("win"):
        app.setStyle("Fusion")
        app_icon = _load_app_icon()
        if not app_icon.isNull():
            app.setWindowIcon(app_icon)

    apply_origin_style()
    theme = AppTheme(app)
    app.setProperty("theme_manager", theme)

    return launch_cme_movie_helper(
        app,
        movie_url=movie_url,
        movie_title=movie_title,
        direct_movie_url=movie_direct_url,
        theme=theme,
        ipc_name=ipc_name,
    )


def _run_goes_overlay_helper_mode(request_file: str, response_file: str) -> int:
    from src.Backend.goes_overlay import run_goes_overlay_helper_cli

    return run_goes_overlay_helper_cli(request_file, response_file)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv)
    args, qt_args = _parse_cli_args(argv)

    if platform.system() != "Windows":
        faulthandler.enable()

    if args.mode == "goes-overlay-helper":
        return _run_goes_overlay_helper_mode(
            request_file=str(args.request_file or ""),
            response_file=str(args.response_file or ""),
        )

    qt_argv = [argv[0], *qt_args]
    app = QApplication(qt_argv)
    sanitize_application_font(app)

    if args.mode == "cme-helper":
        return _run_cme_helper_mode(
            app,
            movie_url=str(args.movie_url or ""),
            movie_title=str(args.movie_title or ""),
            movie_direct_url=str(args.movie_direct_url or ""),
            ipc_name=str(args.ipc_name or ""),
        )

    return _run_main_mode(app)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
