# e-CALLISTO FITS Analyzer  
A desktop application for visualizing, processing, and analyzing e-CALLISTO solar radio FITS data.

---

## 📘 User Guide

This guide explains how to use the main features of the **e-CALLISTO FITS Analyzer**, including dynamic spectrum visualization, noise reduction, burst isolation, drift estimation, maximum intensity extraction, best-fit analysis, and the built-in CME and GOES modules.

---

# 1. Main Interface

After launching the application, the main window appears with controls for loading FITS files, noise thresholds, colormaps, and analysis tools.

### **Main Window**
![Main Window](assets/main_window.png)

---

# 2. Loading a FITS File

Choose **File → Open** or click **Load FITS File** on the sidebar.  
The selected FITS file is displayed as a dynamic spectrum.

---

# 3. Noise Reduction

Use the **Lower Threshold** and **Upper Threshold** sliders to remove background noise or intensity extremes.

Press **Apply Noise Reduction** to update the plot.

### Example: Noise Reduction  
![Noise Reduction](assets/noise_reduction.png)

---

# 4. Burst Isolation (Lasso Tool)

Click **Isolate Burst** and draw a free-form shape around the burst.  
Only the selected region is retained.

### Example: Isolated Burst  
![Isolated Burst](assets/burst_isolation.png)

---

# 5. Maximum Intensities Extraction

After noise reduction or burst isolation, click **Plot Maximum Intensities**.  
This computes the maximum frequency value for each time channel.

### Example: Maximum Intensities  
![Maximum Intensities](assets/maximum_intensity.png)

---

# 6. Outlier Removal

Within the Maximum Intensities window:

- Click **Select Outliers**  
- Draw a lasso around unwanted points  
- Click **Remove Outliers**

This helps clean the dataset before fitting.

---

# 7. Burst Analyzer (Best Fit)

Click **Analyze Burst** to open the Analyzer window.  
The tool performs:

- Power-law fitting  
- Drift rate estimation  
- Shock speed calculation  
- Shock height calculation  
- R² and RMSE metrics  
- Optional extra plots

### Example: Analyzer Window  
![Analyzer](assets/analysis.png)

You may export:

- Best-fit plot (PNG)  
- Data summary (Excel)  
- Shock parameter plots  

---

# 8. FITS Downloader

Access via the **Download** menu → **FITS Downloader**.

Features:

- Select date, hour, and station  
- Show available FITS files on the e-CALLISTO server  
- Preview selected files  
- Download multiple files simultaneously  

### Example: FITS Downloader  
![Downloader](assets/callisto_downloader.png)

---

# 9. Combine FITS Files

Two modes:

### **Combine Frequency**
Merge consecutive frequency-band FITS files from the same station.

### **Combine Time**
Merge back-to-back time segments.

### Example: Combined Time Plot  
![Combine Time](assets/combine_time.png)

The combined file can then be loaded directly into the analyzer.

---

# 10. CME Catalog Viewer (SOHO/LASCO)

Access via the **CME** menu.

Features:

- Retrieve CME catalog for selected date  
- Table of CME parameters  
- Embedded LASCO movie preview  
- Event metadata panel  

### Example: CME Viewer  
![CME Viewer](assets/cme_catalog.png)

---

# 11. GOES X-Ray Flux Viewer

Access via the **Flares** menu.

Features:

- View GOES-16 / GOES-18 short and long channel X-ray flux  
- Adjustable time range  
- Automatic flare parameter extraction  
- Exportable plots and data files  

### Example: GOES X-Ray Viewer  
![GOES X-Ray](assets/goes_xray.png)

---

# 12. Live Noise-Reduced View

When new thresholds are applied, the dynamic spectrum updates automatically.

### Example: Live Noise-Reduced View  
![Noise Reduced Live](assets/noise_reduction.png)

---

## 📄 Notes

- Most tools support exporting PNG images.  
- Maximum intensity and analyzer windows allow Excel export.  
- CME viewer and GOES viewer are non-blocking separate windows.

---

## ⭐ Credits

Developed by **Sahan S. Liyanage**  
Astronomical and Space Science Unit  
University of Colombo, Sri Lanka

---

