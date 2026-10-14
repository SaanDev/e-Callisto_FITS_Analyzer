"""
PyInstaller hook for cdflib.

cdflib decodes the STEREO/WAVES (SWAVES) daily CDF files. ``cdflib/epochs.py``
reads its own ``CDFLeapSeconds.txt`` from disk while the ``CDFepoch`` class is
being defined, and ``cdflib/__init__.py`` imports that module, so a plain
``import cdflib`` opens the file. PyInstaller bundles no data files unless a
hook asks for them, and neither PyInstaller nor pyinstaller-hooks-contrib ships
a cdflib hook, so the Windows and Linux builds raised
``[Errno 2] No such file or directory: '..._internal\\cdflib\\CDFLeapSeconds.txt'``
the first time any SWAVES day was opened.

py2app is unaffected: setup.py lists cdflib under ``packages``, which copies
the whole package directory, data files included.
"""

from PyInstaller.utils.hooks import collect_data_files


datas = collect_data_files("cdflib", include_py_files=False)
