"""
PyInstaller hook for JAX.

``jax`` resolves its backends through entry points and lazily imported
submodules, neither of which PyInstaller follows automatically.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


def _not_tests(name):
    return "tests" not in name.split(".")


datas = collect_data_files("jax", include_py_files=False, excludes=["**/tests/", "**/test/"])
hiddenimports = collect_submodules("jax", filter=_not_tests) + [
    "jax._src.deprecations",
    "jax_plugins",
]
