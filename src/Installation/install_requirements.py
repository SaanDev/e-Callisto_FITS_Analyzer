"""
e-CALLISTO FITS Analyzer
Version 2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

import subprocess
import sys

packages = [
    "PySide6",
    "PySide6-Addons",
    "PySide6-Essentials",
    "matplotlib",
    "pyqtgraph",
    "numpy",
    "pandas",
    "scipy",
    "openpyxl",
    "astropy",
    "scikit-learn",
    "requests",
    "beautifulsoup4",
    "netCDF4",
    "cftime",
    "setuptools",
    "pyinstaller",
    "pyinstaller-hooks-contrib",
]

if sys.platform == "darwin":
    packages.append("py2app")

def install(pkg):
    print(f"Installing {pkg} ...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

def main():
    print("=== Installing all required packages for e-CALLISTO FITS Analyzer ===")
    for pkg in packages:
        try:
            install(pkg)
        except Exception as e:
            print(f"Failed to install {pkg}: {e}")

    print("\nAll installations attempted.")
    print("You can start the application with:\n   python3 src/UI/main.py")

if __name__ == "__main__":
    main()
