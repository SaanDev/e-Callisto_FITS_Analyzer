"""
PyInstaller hook for zeep.

zeep provides the SOAP client used by SunPy's VSO search. It ships WSDL/XSD
template data and uses dynamic imports that PyInstaller misses by default.
"""

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("zeep")
