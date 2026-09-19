"""
PyInstaller hook for imageio-ffmpeg.

MP4 export goes through imageio's FFMPEG writer, which is provided by
imageio-ffmpeg. This package ships the ffmpeg executable inside its
``binaries`` directory and locates it via ``importlib.metadata`` / packaged
data at runtime, so the frozen build needs both the binary and the dist-info
metadata bundled.
"""

from PyInstaller.utils.hooks import collect_all, copy_metadata

datas, binaries, hiddenimports = collect_all("imageio_ffmpeg")
datas += copy_metadata("imageio_ffmpeg")
