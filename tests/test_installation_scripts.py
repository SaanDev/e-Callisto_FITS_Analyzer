import importlib
import os
import sys

from src.Installation import install_requirements


def test_install_requirements_packages_list():
    assert "PySide6" in install_requirements.packages
    assert "matplotlib" in install_requirements.packages
    assert "setuptools" in install_requirements.packages


def test_runtime_hook_sets_linux_env():
    if not sys.platform.startswith("linux"):
        return

    module_name = "src.Installation.pyi_rth_qtwebengine_linux"
    module = importlib.import_module(module_name)
    module = importlib.reload(module)

    assert os.environ.get("QTWEBENGINE_DISABLE_SANDBOX") == "1"
    assert os.environ.get("QT_OPENGL") == "software"
    assert os.environ.get("LIBGL_ALWAYS_SOFTWARE") == "1"
    assert "--disable-gpu" in os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
