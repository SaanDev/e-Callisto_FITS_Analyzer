# e-CALLISTO FITS Analyzer  
A desktop application for visualizing, processing, and analyzing e-CALLISTO solar radio FITS data.

Version **1.7.2**

---

## 📘 User Guide

This guide explains how to use the main features of the **e-CALLISTO FITS Analyzer**, including dynamic spectrum visualization, live noise reduction, burst isolation, drift estimation, maximum intensity extraction, best-fit analysis, the FITS downloader, and the built-in CME and GOES modules.

---

# 1. Main Interface

After launching the application, the main window opens with tools for loading FITS files, adjusting thresholds, selecting colormaps, isolating bursts, and performing scientific analysis.

### **Main Window**
![Main Window](assets/main_window.png)

---

# 2. Loading a FITS File

You can load:

- **Compressed FITS:** `*.fit.gz`  
- **Uncompressed FITS:** `*.fit`

This supports observers who work directly with uncompressed raw data.

Choose **File → Open** or click **Load FITS File**.  
The dynamic spectrum appears immediately.

---

# 3. Noise Reduction (Live Sliders)

Noise reduction now updates **live** without pressing Apply.

Features:

- Two horizontal sliders adjust lower and upper clipping thresholds  
- Dynamic spectrum refreshes automatically  
- No data are lost when switching x-axis units (seconds ↔ UT)

### Example: Noise Reduction  
![Noise Reduction](assets/noise_reduction.png)

---

# 4. Colormap Selection

A new **Colormap** panel allows choosing from several scientifically useful palettes:

- Custom (blue-red-yellow)
- Viridis  
- Plasma  
- Inferno  
- Magma  
- Cividis  
- Turbo  
- RdYlBu  
- Jet  
- Cubehelix  

The plot updates as soon as a colormap is selected.

---

# 5. Burst Isolation (Lasso Tool)

Click **Isolate Burst** and draw around the emission region.  
Only the selected region is retained for further analysis.

### Example: Isolated Burst  
![Isolated Burst](assets/burst_isolation.png)

---

# 6. Maximum Intensities Extraction

Click **Plot Maximum Intensities** to compute the maximum frequency for each time channel after noise reduction or burst isolation.

### Example: Maximum Intensities  
![Maximum Intensities](assets/maximum_intensity.png)

---

# 7. Outlier Removal

Inside the Maximum Intensities window:

- Draw a lasso to select outliers  
- Remove them instantly  
- Prepare the cleaned curve for fitting

---

# 8. Burst Analyzer (Best Fit & Shock Parameters)

The Analyzer window performs:

- Power-law fitting of the Type II backbone  
- Drift-rate evaluation  
- Shock speed  
- Shock height  
- R² and RMSE  
- Optional additional plots:
  - Shock speed vs height  
  - Shock speed vs frequency  
  - Height vs frequency  

### Example: Analyzer  
![Analyzer](assets/analysis.png)

Export options:

- Best-fit graph (PNG, PDF, EPS, SVG, TIFF)  
- Data summary to Excel  
- Multiple additional plots  

---

# 9. FITS Downloader (Updated)

Open via **Download → FITS Downloader**.

Features:

- Select station, date, hour  
- Fetch available files from server  
- Preview selected files  
- Download multiple FITS files  
- **New:** Import button to send selected FITS files directly into the Analyzer  
- Detects whether files match frequency or time stitching requirements  
- Shows an error message if selected files cannot be combined  

### Example: Downloader  
![Downloader](assets/callisto_downloader.png)

---

# 10. Combine FITS Files

Two combination modes:

### **Combine Frequency**
Merge consecutive frequency bands when time bases match.

### **Combine Time**
Merge consecutive time segments from the same station and date.

If files do not meet the expected criteria, a message box alerts the user.

### Example: Combined Time Plot  
![Combine Time](assets/combine_time.png)

Combined data can be imported directly to the Analyzer.

---

# 11. Saving Plots (New Formats)

All figures across the application can be exported in:

- PNG  
- PDF  
- EPS  
- SVG  
- TIFF  

This makes the tool ready for scientific publication workflows.

---

# 12. CME Catalog Viewer (SOHO/LASCO)

Features:

- Retrieve daily CME list  
- Display CME parameters in a structured table  
- Show associated LASCO movie  
- Event metadata panel  

### Example: CME Viewer  
![CME Viewer](assets/cme_catalog.png)

---

# 13. GOES X-Ray Flux Viewer

Features:

- View GOES-16 / GOES-18 light curves  
- Select short or long channels  
- Adjust time windows  
- Extract flare parameters  
- Export plots and data  

### Example: GOES X-Ray Viewer  
![GOES X-Ray](assets/goes_xray.png)

---

# 14. Live Noise-Reduced View

When thresholds are changed, the spectrum updates live while keeping the chosen axis format (seconds or UT).

---

## 📄 Notes

- Supports both `.fit` and `.fit.gz` files.  
- Noise reduction is preserved when switching x-axis units.  
- All major plots allow exporting in publication-ready formats.  
- Analyzer now includes improved shock parameter calculations and Excel export.

---

## ⭐ Credits

Developed by **Sahan S. Liyanage**  
Astronomical and Space Science Unit  
University of Colombo, Sri Lanka
