"""
PyInstaller hook for jaxlib.

jaxlib ships compiled extension modules and a PJRT plugin directory that
PyInstaller's import analysis does not discover on its own. Without these the
packaged app raises on ``import jax`` and silently drops to the NumPy backend.

CUDA libraries are deliberately not collected: the packaged builds are CPU-only
and users opt into GPU support separately (see requirements-gpu.txt).
"""

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


def _not_tests(name):
    return "tests" not in name.split(".")


binaries = collect_dynamic_libs("jaxlib")
datas = collect_data_files("jaxlib", excludes=["**/tests/", "**/test/"])
hiddenimports = collect_submodules("jaxlib", filter=_not_tests)
