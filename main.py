import sys
import os
from PySide6.QtWidgets import QApplication
from gui_main import MainWindow
import faulthandler

# Fix for PyInstaller GUI mode when console=False
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')

faulthandler.enable()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
