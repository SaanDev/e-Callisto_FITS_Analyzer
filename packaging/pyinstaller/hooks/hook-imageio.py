"""
PyInstaller hook for imageio.

imageio's ``__init__`` calls ``importlib.metadata.version("imageio")`` at import
time. Freezing the module without its ``.dist-info`` metadata makes that call
raise ``PackageNotFoundError``, which surfaced as a "Movie export requires
imageio" RuntimeError in packaged builds even though the module was bundled.

``copy_metadata`` bundles the dist-info so the version lookup succeeds, and
``collect_all`` pulls in the plugin submodules/data imageio loads lazily.
"""

from PyInstaller.utils.hooks import collect_all, copy_metadata

datas, binaries, hiddenimports = collect_all("imageio")
datas += copy_metadata("imageio")
