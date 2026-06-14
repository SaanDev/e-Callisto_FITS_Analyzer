"""
PyInstaller hook for parfive.

parfive is SunPy's async downloader (built on aiohttp). Collect its modules,
data, and binaries so Fido.fetch works in frozen builds.
"""

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("parfive")
