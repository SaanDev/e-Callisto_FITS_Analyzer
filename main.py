"""
e-CALLISTO FITS Analyzer
Version 1.7.2
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

import sys
from PySide6.QtWidgets import QApplication
from gui_main import MainWindow
import faulthandler

if sys.stderr:
    faulthandler.enable()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    window.showMaximized()
    sys.exit(app.exec())