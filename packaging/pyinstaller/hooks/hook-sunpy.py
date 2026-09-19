"""
PyInstaller hook for SunPy.
Collect SunPy data files and submodules required at runtime for net/map/timeseries workflows.

The ``tests`` packages are skipped on purpose: importing them triggers
``pytest.importorskip(...)`` calls (e.g. sunpy.io.special.asdf.tests requires the
optional ``asdf`` package), which raise ``Skipped`` and abort the PyInstaller build.
They are not needed at runtime anyway.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


def _not_tests(name):
    return "tests" not in name.split(".")


datas = collect_data_files(
    "sunpy", include_py_files=False, excludes=["**/tests/", "**/test/"]
)
hiddenimports = collect_submodules("sunpy", filter=_not_tests)

