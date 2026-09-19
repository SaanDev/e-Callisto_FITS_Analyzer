"""
PyInstaller hook for aiapy.

aiapy provides AIA level 1.5 (``aiapy.calibrate.register``/``update_pointing``)
in the Solar Image Analysis window. ``aiapy/__init__.py`` reads its own
``CITATION.rst`` from disk at import time, and PyInstaller bundles no data files
unless a hook asks for them, so ``import aiapy`` raised ``FileNotFoundError`` in
the Windows and Linux builds. The level-1.5 code reported that as "Install it
with: python3 -m pip install aiapy" even though the module was bundled.

py2app is unaffected: its ``detect_dunder_file`` recipe copies any package that
reads ``__file__`` as a whole directory, data files included.

The ``tests`` packages and ``conftest`` are skipped, as in hook-sunpy.py: they
import pytest and are not needed at runtime.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


def _not_tests(name):
    parts = name.split(".")
    return "tests" not in parts and "conftest" not in parts


datas = collect_data_files(
    "aiapy", include_py_files=False, excludes=["**/tests/", "**/test/"]
)
hiddenimports = collect_submodules("aiapy", filter=_not_tests)
