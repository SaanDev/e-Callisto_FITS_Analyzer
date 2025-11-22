import sys
from PySide6.QtWidgets import QApplication
from gui_main import MainWindow
import faulthandler
import sys

if sys.stderr:
    faulthandler.enable()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
