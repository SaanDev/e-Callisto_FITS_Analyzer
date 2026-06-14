"""
PyInstaller hook for drms.

drms is used by SunPy to query/export JSOC data (SDO/AIA, SDO/HMI). Collect its
submodules and any packaged data so frozen builds can search/fetch JSOC records.
"""

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("drms")
