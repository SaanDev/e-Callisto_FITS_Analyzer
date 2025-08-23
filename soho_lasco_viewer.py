import sys
import requests
from bs4 import BeautifulSoup
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QTableWidget, QTableWidgetItem, QSplitter,
    QTextEdit, QProgressBar
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtCore import Qt, QUrl, QThread, Signal
from datetime import datetime, timedelta

BASE_URL = "https://cdaw.gsfc.nasa.gov/CME_list/UNIVERSAL_ver2/"

class FetchThread(QThread):
    progress = Signal(int)
    result = Signal(list)

    def __init__(self, year, month, day):
        super().__init__()
        self.year, self.month, self.day = year, month, day

    def run(self):
        url = f"{BASE_URL}{self.year}_{self.month}/univ{self.year}_{self.month}.html"
        try:
            self.progress.emit(10)
            resp = requests.get(url)
            self.progress.emit(30)
            if resp.status_code != 200:
                self.result.emit([])
                return

            soup = BeautifulSoup(resp.text, "html.parser")
            all_rows = soup.find_all("tr")
            start_index = 0
            for idx, row in enumerate(all_rows):
                if "First C2 Appearance" in row.get_text():
                    start_index = idx + 1
                    break

            target = f"{self.year}/{self.month}/{self.day}"
            data = []

            for row in all_rows[start_index:]:
                cols = row.find_all("td")
                if not cols or len(cols) < 13:
                    continue

                row_date = cols[0].get_text(strip=True)
                row_time = cols[1].get_text(strip=True)
                if row_date != target:
                    continue

                datetime_str = f"{row_date} {row_time}"
                values = [
                    datetime_str,
                    cols[2].get_text(strip=True),
                    cols[3].get_text(strip=True),
                    cols[4].get_text(strip=True),
                    cols[7].get_text(strip=True),
                    cols[8].get_text(strip=True),
                    cols[9].get_text(strip=True),
                    cols[10].get_text(strip=True),
                    cols[12].get_text(strip=True),
                ]

                link = None
                if cols[11]:
                    a = cols[11].find("a")
                    if a:
                        link = a["href"]

                data.append((values, link))

            self.progress.emit(100)
            self.result.emit(data)

        except Exception:
            self.result.emit([])

class CMEViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SOHO/LASCO CME Catalog Tool")
        self.resize(1400, 900)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # --- Date selection ---
        date_layout = QHBoxLayout()
        self.year_combo = QComboBox()
        self.month_combo = QComboBox()
        self.day_combo = QComboBox()
        for y in range(1996, datetime.now().year + 1):
            self.year_combo.addItem(str(y))
        for m in range(1, 13):
            self.month_combo.addItem(f"{m:02d}")
        for d in range(1, 32):
            self.day_combo.addItem(f"{d:02d}")
        date_layout.addWidget(QLabel("Year:")); date_layout.addWidget(self.year_combo)
        date_layout.addWidget(QLabel("Month:")); date_layout.addWidget(self.month_combo)
        date_layout.addWidget(QLabel("Day:")); date_layout.addWidget(self.day_combo)
        self.search_btn = QPushButton("Search")
        date_layout.addWidget(self.search_btn)
        layout.addLayout(date_layout)

        # --- Progress bar ---
        self.progress = QProgressBar()
        layout.addWidget(self.progress)

        # --- Top-bottom splitter ---
        outer_splitter = QSplitter(Qt.Vertical)
        layout.addWidget(outer_splitter)

        # --- Top: Table and details ---
        top_splitter = QSplitter(Qt.Horizontal)
        outer_splitter.addWidget(top_splitter)

        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels([
            "Date and Time", "Central PA", "Angular Width", "Linear Speed",
            "Accel", "Mass", "Kinetic Energy", "MPA", "Remarks"
        ])
        top_splitter.addWidget(self.table)

        self.details_text = QTextEdit()
        self.details_text.setReadOnly(True)
        top_splitter.addWidget(self.details_text)

        # --- Bottom: WebView for movie ---
        self.movie_view = QWebEngineView()
        self.movie_view.setHtml("<h3 style='color:gray; text-align:center;'>Movie Area</h3>")
        outer_splitter.addWidget(self.movie_view)

        # Resize priorities
        outer_splitter.setStretchFactor(0, 3)  # Table + details
        outer_splitter.setStretchFactor(1, 2)  # Movie area

        # Connections
        self.search_btn.clicked.connect(self.search_cmes)
        self.table.cellClicked.connect(self.show_cme_details)
        self.table.itemDoubleClicked.connect(self.play_cme_movie)

    def search_cmes(self):
        y = self.year_combo.currentText()
        m = self.month_combo.currentText()
        d = self.day_combo.currentText()
        self.progress.setValue(0)
        self.details_text.clear()
        self.table.setRowCount(0)
        self.movie_view.setHtml("<h3 style='color:gray; text-align:center;'>Movie Area</h3>")

        self.fetch_thread = FetchThread(y, m, d)
        self.fetch_thread.progress.connect(self.progress.setValue)
        self.fetch_thread.result.connect(self.populate_table)
        self.fetch_thread.start()

    def populate_table(self, data):
        self.table.setRowCount(0)
        if not data:
            self.details_text.setText("No CME data available for selected date.")
            self.movie_view.setHtml("<h3 style='color:gray;'>No Data</h3>")
            return

        for values, link in data:
            row = self.table.rowCount()
            self.table.insertRow(row)
            for i, val in enumerate(values):
                self.table.setItem(row, i, QTableWidgetItem(val))
            if link:
                self.table.item(row, 0).setData(Qt.UserRole, link)

    def show_cme_details(self, row, _):
        headers = [self.table.horizontalHeaderItem(i).text()
                   for i in range(self.table.columnCount())]
        details = [
            f"{headers[i]}: {self.table.item(row, i).text()}"
            for i in range(self.table.columnCount())
        ]
        self.details_text.setText("\n".join(details))

    def play_cme_movie(self, item):
        row = item.row()
        dt_text = self.table.item(row, 0).text()
        try:
            start_dt = datetime.strptime(dt_text, "%Y/%m/%d %H:%M:%S")
            end_dt = start_dt + timedelta(hours=2)
            stime = start_dt.strftime("%Y%m%d_%H%M")
            etime = end_dt.strftime("%Y%m%d_%H%M")
            url = f"https://cdaw.gsfc.nasa.gov/movie/make_javamovie.php?stime={stime}&etime={etime}&img1=lasc2rdf"
            self.movie_view.setUrl(QUrl(url))
        except Exception as e:
            self.movie_view.setHtml(f"<h3 style='color:red;'>Error loading movie</h3><p>{e}</p>")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = CMEViewer()
    win.show()
    sys.exit(app.exec())
