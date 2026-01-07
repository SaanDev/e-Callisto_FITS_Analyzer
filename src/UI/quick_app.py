"""
e-CALLISTO FITS Analyzer
Version 1.7.4
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from PySide6.QtCore import QObject, Slot

from src.UI.callisto_downloader import CallistoDownloaderApp
from src.UI.goes_xrs_gui import MainWindow as GoesXrsWindow
from src.UI.gui_main import MainWindow
from src.UI.soho_lasco_viewer import CMEViewer


class AppController(QObject):
    def __init__(self):
        super().__init__()
        self._analyzer_window = None
        self._downloader_window = None
        self._goes_window = None
        self._lasco_window = None

    def _show_window(self, window, maximized=False):
        if maximized:
            window.showMaximized()
        else:
            window.show()
        window.raise_()
        window.activateWindow()

    @Slot()
    def openAnalyzer(self):
        if self._analyzer_window is None:
            self._analyzer_window = MainWindow()
        self._show_window(self._analyzer_window, maximized=True)

    @Slot()
    def openDownloader(self):
        if self._downloader_window is None:
            self._downloader_window = CallistoDownloaderApp()
        self._show_window(self._downloader_window)

    @Slot()
    def openGoesXrs(self):
        if self._goes_window is None:
            self._goes_window = GoesXrsWindow()
        self._show_window(self._goes_window)

    @Slot()
    def openLascoViewer(self):
        if self._lasco_window is None:
            self._lasco_window = CMEViewer()
        self._show_window(self._lasco_window)
