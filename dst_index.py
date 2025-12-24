"""
e-CALLISTO FITS Analyzer
Version 1.7.4
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

import sys
import pandas as pd
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QDateEdit, QTextEdit
)
from PySide6.QtCore import QDate
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.widgets import RectangleSelector
from matplotlib.dates import num2date


class DstPlotter(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dst Index Plotter")
        self.setGeometry(200, 200, 1000, 700)

        # Main widget
        widget = QWidget()
        self.setCentralWidget(widget)
        layout = QVBoxLayout(widget)

        # Controls layout
        controls = QHBoxLayout()

        self.load_btn = QPushButton("Load .dat File")
        self.load_btn.clicked.connect(self.load_file)
        controls.addWidget(self.load_btn)

        controls.addWidget(QLabel("Start Date:"))
        self.start_date = QDateEdit()
        self.start_date.setCalendarPopup(True)
        self.start_date.setDate(QDate(2020, 1, 1))
        controls.addWidget(self.start_date)

        controls.addWidget(QLabel("End Date:"))
        self.end_date = QDateEdit()
        self.end_date.setCalendarPopup(True)
        self.end_date.setDate(QDate(2020, 1, 31))
        controls.addWidget(self.end_date)

        self.plot_btn = QPushButton("Plot")
        self.plot_btn.clicked.connect(self.plot_data)
        controls.addWidget(self.plot_btn)

        layout.addLayout(controls)

        # Matplotlib Figure
        self.figure = Figure(figsize=(8, 5))
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas)

        # Display area for results
        self.result_display = QTextEdit()
        self.result_display.setReadOnly(True)
        self.result_display.setPlaceholderText("Storm analysis results will appear here...")
        layout.addWidget(self.result_display)

        # DataFrame placeholder
        self.dst_df = None
        self.rectangle_selector = None

    def load_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Dst .dat File", "", "DAT Files (*.dat);;All Files (*)")
        if not file_path:
            return

        # Parse the IAGA-2002 formatted file
        data_lines = []
        with open(file_path, "r") as f:
            for line in f:
                if line.strip() == "" or line.startswith("#") or line.startswith("Format"):
                    continue
                if line.startswith("DATE"):
                    continue
                parts = line.split()
                if len(parts) >= 4:
                    date, time, doy, dst = parts[0], parts[1], parts[2], parts[3]
                    try:
                        ts = pd.to_datetime(date + " " + time)
                        dst_val = float(dst)
                        data_lines.append([ts, dst_val])
                    except:
                        continue

        self.dst_df = pd.DataFrame(data_lines, columns=["datetime", "Dst"])
        if not self.dst_df.empty:
            self.start_date.setDate(QDate(self.dst_df["datetime"].dt.date.min()))
            self.end_date.setDate(QDate(self.dst_df["datetime"].dt.date.max()))

    def plot_data(self):
        if self.dst_df is None or self.dst_df.empty:
            return

        start = pd.to_datetime(self.start_date.date().toString("yyyy-MM-dd"))
        end = pd.to_datetime(self.end_date.date().toString("yyyy-MM-dd"))

        self.subset = self.dst_df[(self.dst_df["datetime"] >= start) & (self.dst_df["datetime"] <= end)]

        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.plot(self.subset["datetime"], self.subset["Dst"], color="blue", label="Dst Index")
        ax.axhline(y=0, color="black", linestyle="--", linewidth=0.8)
        ax.set_ylim(-500, 200)
        ax.set_title(f"Dst Index {start.date()} to {end.date()}")
        ax.set_xlabel("Date")
        ax.set_ylabel("Dst [nT]")
        ax.legend()
        ax.grid(True)

        # Rectangle selector (updated API, no drawtype)
        self.rectangle_selector = RectangleSelector(
            ax, self.on_select,
            useblit=True,
            button=[1],  # left mouse button
            minspanx=1, minspany=1,
            spancoords="pixels",
            interactive=True
        )

        self.canvas.draw()

    def on_select(self, eclick, erelease):
        if self.subset is None or self.subset.empty:
            return

        # Convert from matplotlib float time to pandas datetime (remove timezone)
        start_time = pd.to_datetime(num2date(min(eclick.xdata, erelease.xdata))).tz_localize(None)
        end_time = pd.to_datetime(num2date(max(eclick.xdata, erelease.xdata))).tz_localize(None)

        # Filter the selected interval
        selected = self.subset[(self.subset["datetime"] >= start_time) &
                               (self.subset["datetime"] <= end_time)]

        if selected.empty:
            self.result_display.setText("No data in selected region.")
            return

        # Find storm details
        min_dst = selected["Dst"].min()
        min_time = selected.loc[selected["Dst"].idxmin(), "datetime"]

        # Display results
        result_text = (
            f"Storm Interval: {start_time} → {end_time}\n"
            f"Peak Dst (minimum): {min_dst} nT at {min_time}\n"
            f"Duration: {end_time - start_time}\n"
            f"Data points: {len(selected)}\n"
        )
        self.result_display.setText(result_text)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DstPlotter()
    window.show()
    sys.exit(app.exec())
