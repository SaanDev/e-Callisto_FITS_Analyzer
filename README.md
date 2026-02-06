# e-CALLISTO FITS Analyzer
A desktop application for visualizing, processing, and analyzing e-CALLISTO solar radio FITS data.

Version **1.7.7**

---

## 🆕 What's New (v1.7.7)

### Core workflow
- Added **session/project save and load** support to restore analysis state.
- Introduced a unified, more robust FITS I/O layer across loading/downloading/combining workflows.
- Improved FITS parsing for `.fit`, `.fits`, `.fit.gz`, `.fits.gz`, swapped axes, and missing/alternate axis tables.
- Added **FITS View → View FITS Header** with header inspection and save-to-`.txt`.
- Combined datasets now carry updated header metadata for time/frequency merge context.

### Analysis and plotting
- Fixed Best-Fit/Analyzer crash (`TypeError: cannot unpack non-iterable bool object`) in shock parameter workflows.
- Dynamic spectrum titles are now context-aware (`filename-Raw`, `filename-Background Subtracted`) and no longer use `Dynamic Spectrum`.
- Short-duration time windows now show `hh:mm:ss` for better readability.
- Fixed undo/reset-view behavior after zooming so actions stay enabled and functional.
- Added **Edit → Reset to Raw** to restore raw data view and reset clipping sliders to `0`.

### Export and compatibility
- Improved exported FITS compatibility with external tools (including JavaViewer) for combined files.
- Added export `BITPIX` control (`Auto`, `8`, `16`, `32`) and removed problematic float64 export behavior.
- FITS export now reuses source FITS structure for combined output to preserve axis-table compatibility.
- Default export filenames (image + FITS) now follow the active graph title and avoid duplicated combine suffixes.

### UI/UX
- Added a **collapsible left sidebar**.
- When collapsed, the graph area expands to use the full available width.

---

## 📘 User Guide

This guide explains how to use the main features of the **e-CALLISTO FITS Analyzer**, including dynamic spectrum visualization, live noise reduction, burst isolation, drift estimation, maximum intensity extraction, best-fit analysis, FITS export, the FITS downloader, and the built-in CME and GOES modules.

---

# 1. Main Interface

After launching the application, the main window opens with tools for loading FITS files, adjusting thresholds, selecting colormaps, isolating bursts, navigating the spectrum, and performing scientific analysis.

The main functions are available through a compact **icon toolbar** for quick access and a clean layout.

### **Main Window**
![Main Window](assets/main_window.png)

---

# 2. Loading a FITS File

You can load:

- **Compressed FITS:** `*.fit.gz`
- **Uncompressed FITS:** `*.fit`

This supports observers who work directly with uncompressed raw data.

Choose **File → Open** or click the **Open** icon on the toolbar.  
The dynamic spectrum appears immediately.

---

# 3. Noise Reduction (Live Threshold Scrollbars)

Noise reduction updates **live** without pressing Apply.

Features:

- Two wide horizontal scrollbars for lower and upper clipping thresholds (Vmin / Vmax)
- Labels repositioned for clearer visual feedback
- Dynamic spectrum refreshes automatically
- No data are lost when switching x-axis units (seconds ↔ UT)

### Example: Noise Reduction
![Noise Reduction](assets/noise_reduction.png)

---

# 4. Intensity Scale and Units

The color-bar (z-axis) provides clearer physical meaning.

Features:

- Explicit intensity labeling on the color-bar
- Unit selector for:
  - **Digits / ADU**
  - **Optional dB scaling**
- Unit changes update the display immediately

This improves interpretability across different observing stations.

---

# 5. Colormap Selection

The **Colormap** panel allows choosing from several scientifically useful palettes:

- Custom (blue–red–yellow)
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

# 6. Graph Properties Panel

A **Graph Properties** panel is included to adjust plot appearance from one place.

Typical use cases:

- Update titles and labels for exports
- Adjust plot styling for clearer presentation
- Keep visual settings consistent across plots

---

# 7. Navigation: Zoom and Pan

Interactive navigation is available in the dynamic spectrum.

Features:

- **Scroll wheel:** Zoom in and out
- **Click + drag:** Pan across time and frequency
- Navigation works alongside noise reduction and colormap changes

This allows precise inspection of fine spectral structures.

---

# 8. Cursor Data Display

When moving the mouse cursor over the plot area, the status bar displays:

- Time
- Frequency
- Intensity value (in selected units)

This enables quick quantitative inspection without additional clicks.

---

# 9. Burst Isolation (Lasso Tool)

Click **Isolate Burst** and draw around the emission region.  
Only the selected region is retained for further analysis.

### Example: Isolated Burst
![Isolated Burst](assets/burst_isolation.png)

---

# 10. Maximum Intensities Extraction

Click **Plot Maximum Intensities** to compute the maximum frequency for each time channel after noise reduction or burst isolation.

### Example: Maximum Intensities
![Maximum Intensities](assets/maximum_intensity.png)

---

# 11. Outlier Removal

Inside the Maximum Intensities window:

- Draw a lasso to select outliers
- Remove them instantly
- Prepare the cleaned curve for fitting

---

# 12. Burst Analyzer (Best Fit & Shock Parameters)

The Analyzer window performs:

- Power-law fitting of the Type II backbone
- Drift-rate evaluation
- Shock speed
- Shock height
- R² and RMSE

Newkirk model option:

- **Newkirk fold number** can be selected as:
  - **1, 2, 3, 4**

Optional additional plots:

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

# 13. FITS Downloader

Open via **Download → FITS Downloader**.

Features:

- Select station, date, and hour
- Fetch available files from the server
- Preview selected files
- Download multiple FITS files
- Import selected FITS files directly into the Analyzer
- Automatic detection of frequency or time stitching compatibility
- Clear error messages when selected files cannot be combined

### Example: Downloader
![Downloader](assets/callisto_downloader.png)

![Downloader](assets/callisto_downloader_preview.png)

---

# 14. Combine FITS Files

Two combination modes are supported.

### **Combine Frequency**
Merge consecutive frequency bands when time bases match.

### **Combine Time**
Merge consecutive time segments from the same station and date.

If files do not meet the required criteria, a message box alerts the user.

Combined data can be imported directly into the Analyzer.

---

# 15. Export Data as FITS

You can now export processed data as a new FITS file with a modified header. This is useful for downstream analysis and **Machine Learning** workflows.

Export options:

- **Raw**
- **Combined**
- **Background-subtracted**

Path:

- **File → Export As → Export to FIT**

---

# 16. Saving and Exporting Plots

All figures across the application can be exported in:

- PNG
- PDF
- EPS
- SVG
- TIFF

Export handling improvements:

- Export errors for PDF, EPS, and SVG formats have been resolved
- On Windows, if the default save location is restricted (for example `C:\Program Files`), the user is prompted to select an alternate folder

This supports publication workflows across operating systems.

---

# 17. CME Catalog Viewer (SOHO/LASCO)

Features:

- Retrieve daily CME lists
- Display CME parameters in a structured table
- Show associated LASCO movies
- Event metadata panel

### Example: CME Viewer
![CME Viewer](assets/cme_catalog.png)

---

# 18. GOES X-Ray Flux Viewer

Features:

- View GOES-16 / GOES-18 X-ray light curves
- Select short or long channels
- Adjust time windows
- Extract flare parameters
- Export plots and data

### Example: GOES X-Ray Viewer
![GOES X-Ray](assets/goes_xray.png)

---

## 📄 Notes

- Supports both `.fit` and `.fit.gz` files
- Live noise reduction with preserved zoom, pan, and axis format
- Cursor-based data readout for time, frequency, and intensity
- Improved plotting area for clearer scientific visualization
- Robust export system with OS-aware save handling
- Major plots are publication ready

---

## ⭐ Credits

Developed by **Sahan S. Liyanage**  
Astronomical and Space Science Unit  
University of Colombo, Sri Lanka
