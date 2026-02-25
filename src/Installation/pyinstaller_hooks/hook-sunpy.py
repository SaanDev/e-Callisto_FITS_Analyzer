"""
PyInstaller hook for SunPy.
Collect SunPy data files and submodules required at runtime for net/map/timeseries workflows.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


datas = collect_data_files("sunpy", include_py_files=False)
hiddenimports = collect_submodules("sunpy")

