"""
e-CALLISTO FITS Analyzer
Version 1.7.4
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

import sys
import platform
from PySide6.QtWidgets import QApplication
from gui_main import MainWindow
import faulthandler

if platform.system() != "Windows":
    faulthandler.enable()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.showMaximized()
    #window.show()
    sys.exit(app.exec())


