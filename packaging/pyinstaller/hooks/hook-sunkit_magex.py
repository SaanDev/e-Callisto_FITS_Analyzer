"""
PyInstaller hook for sunkit-magex (and its streamtracer dependency).

sunkit-magex provides the PFSS coronal field extrapolation in the Solar Image
Analysis window. It is imported lazily from ``src.backend.solar.pfss_model``, so
static analysis cannot see it.

Two things here are not automatic:

* ``streamtracer`` is a **compiled** extension (the field-line tracer), and it is
  a hard dependency of sunkit-magex rather than an optional extra. Its shared
  library has to be collected explicitly with ``collect_dynamic_libs``, or
  ``import sunkit_magex.pfss`` fails at runtime with a missing-module error that
  looks exactly like "the package was not installed".
* ``streamtracer`` also reads **its own installed metadata** at import time:
  ``streamtracer/version.py`` calls ``importlib.metadata.version("streamtracer")``
  on line 5. PyInstaller bundles a package's ``.dist-info`` only when a hook asks
  via ``copy_metadata``, so without it the frozen app raises
  ``PackageNotFoundError: No package metadata was found for streamtracer`` the
  moment ``sunkit_magex.pfss`` is imported. Verified 2026-09-21 with a minimal
  onedir probe: the bundle failed identically with and without the rest of this
  hook until ``copy_metadata`` was added. ``sunkit_magex`` itself needs no
  equivalent -- its version is a static ``_version.py`` written at build time.
* sunkit-magex ships data files alongside its modules, and PyInstaller bundles
  none unless a hook asks.

``scikit-image`` (used for the polarity-inversion-line contours) needs no entry
here: pyinstaller-hooks-contrib already ships ``hook-skimage.py`` and
``hook-skimage.measure.py``, which handle its ``lazy_loader`` ``.pyi`` stubs.

The ``tests`` packages and ``conftest`` are skipped, as in hook-sunpy.py and
hook-aiapy.py: they import pytest and are not needed at runtime.
"""

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)


def _not_tests(name):
    parts = name.split(".")
    return "tests" not in parts and "conftest" not in parts


datas = collect_data_files(
    "sunkit_magex", include_py_files=False, excludes=["**/tests/", "**/test/"]
)
hiddenimports = collect_submodules("sunkit_magex", filter=_not_tests) + [
    "streamtracer",
    "lazy_loader",
]

# The compiled tracer. Without this the bundled app imports sunkit_magex and
# then dies on the first solve.
binaries = collect_dynamic_libs("streamtracer")

# streamtracer reads its own dist-info on import; see the note above.
datas += copy_metadata("streamtracer")
