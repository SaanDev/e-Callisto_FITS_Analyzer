# e-CALLISTO FITS Analyzer (v2.7.0)
A desktop application for visualizing, processing, and analyzing e-CALLISTO solar radio FITS data.

---

## What's New in v2.7.0

Compared with v2.6.0, this release adds the following capabilities:

### Solar Image Analysis workspace
- **Multi-mission imaging:** the former SDO/AIA workspace has grown into **Analysis → Solar Image Analysis (SDO · SOHO/LASCO · STEREO · GOES/SUVI)**, supporting SDO/AIA and HMI, SOHO/LASCO C2/C3, STEREO-A/B SECCHI (EUVI, COR1, COR2, HI1, HI2), and GOES/SUVI — searched and downloaded through the shared SunPy cache or loaded from local FITS/FITS.GZ files.
- **Instrument-adaptive interface:** the sidebar shows only the tools relevant to each observable (disk EUV, coronagraph, heliospheric, magnetograph), with a load-summary label, a live solar-coordinate cursor readout (R☉ · position angle), and an optional solar-coordinate graticule overlay.
- **Measurement tools:** ruler, intensity profile, ROI region statistics, and CME height-time tracking with a live fit panel and one-click-per-frame auto-advance.
- **Coronagraph, heliospheric, and magnetogram science:** NRGF and radial-graded normalization, radial cuts, HI time-elongation J-maps, and HMI vector-field processing.
- **Correct differences across mixed frames:** running/base differences partition mixed observing configurations (for example COR2 polarizer vs total-brightness frames) and exposure-normalize to DN/s so cross-configuration differences render accurately.
- **Compare Viewpoint:** fetch a second viewpoint near the same time and reproject it onto the primary view for blink comparison.
- **Helioviewer previews** for quick SOHO/LASCO context, plus AIA RGB composites and plot/cropped-FITS/region-CSV/GIF/MP4 exports.
- **Self-contained sessions:** save and reopen the full workspace as an `.ecsolar` file (Session menu, `Ctrl+S`) that embeds the original FITS bytes and restores frames, view state, crop, and CME height-time picks exactly.

### Multi-station analysis
- **Multi-Station Comparison workspace:** open multiple FITS files in stacked synchronized panels, align them by UT clock or seconds from file start, and automatically combine compatible time/frequency files per station.
- **Comparison noise-reduction controls:** apply mean, median, robust, or clipping-based noise reduction to every panel or an individual panel, with live threshold previews and synchronized colormaps.
- **Flexible comparison exports:** export the visible comparison or a compact publication-style grid with shared display ranges and configurable shared, per-station, or manual color scaling.
- **Multi-Station Event downloader:** search selected stations across a UTC event window, download matching FITS files, and send the selected or downloaded files directly to the comparison workspace. The standard downloader also includes a direct **Compare** action.

### Spectrum tools and reproducible views
- **Ruler measurements:** click two points on the main spectrum or a comparison panel to measure duration, frequency change, and drift slope.
- **Aligned spectrum view workflow:** enter exact time/frequency display ranges, save and reuse range presets, and export/import complete `.efaview.json` view configurations.
- **Locked batch exports:** apply the current display range or a saved view configuration when batch-exporting spectra so multiple outputs use consistent axes and styling.

### Downloader and median_dB workflows
- **Full-day Spectral Overview:** generate and export a station's complete UTC-day spectrum as six organized four-hour panels using a day-wide median_dB background baseline.
- **Focus-code overview tabs:** generate previews for every available receiver/focus code for the selected station and date, or regenerate one selected code.
- **median_dB processing:** use the median_dB digit-to-dB scale and display range in batch processing, spectral overviews, and downloader previews.

### In-app help and small screens
- **Help → User Guide** (F1) opens the full feature walkthrough inside the app.
- Dialogs and the main window now fit-to-screen with clamped minimum sizes so the app remains usable on small or low-resolution displays.

### Reliability and packaging
- Fixed FITS-load default preset application so configured defaults apply without unintended intermediate replots.
- Improved multi-station rendering, noise previews, station/date labels, automatic combination, and visible/grid export layouts.
- Hardened the multi-mission pipeline: STEREO/SECCHI archive routing, GOES/SUVI ingestion of malformed `CONTINUE`-card headers, sensible default GOES satellites, and a Windows fix for solar image panels rendering solid black under hardware OpenGL.
- Added Windows PySide6 environment repair and validation tooling, packaged the new v2.7.0 modules across platforms, and added an opt-in Linux XCB fallback for affected Wayland setups.

---

## ✨ Current Feature Highlights

### Dynamic spectrum workflow
- Load `.fit`, `.fits`, `.fit.gz`, and `.fits.gz` files, including combined time/frequency datasets.
- Download and analyze e-CALLISTO and Learmonth Station radio data, including Learmonth chunk conversion to FIT format for the main Analyzer.
- Use hardware-accelerated plotting with live cursor readouts, rectangular zoom, lock/unlock navigation, and **Edit → Reset to Raw** controls.
- Adjust intensity thresholds live with high-resolution sliders, value readouts, optional signed-log scaling, dB or Digits/ADU display modes, and graph-property controls.
- Apply the **Raw FITS Percentile (5-98%)** noise-clipping preset from **Processing → Presets** for a fast starting display range on raw FITS files.
- Inspect FITS headers from the **View** menu, customize titles and labels, and export publication-ready figures from the current analysis view.
- Generate project report PDFs that summarize the loaded dataset, processing state, analysis outputs, solar-context plots, and report-ready figures.

### Processing and analysis
- Apply deterministic RFI cleaning with preview/apply/reset controls for median smoothing, hot-channel masking, masked-channel repair, and percentile clipping.
- Isolate radio bursts with lasso masking aligned to the rendered spectrum path, extract maximum intensities, remove outliers manually or automatically, run best-fit / shock-parameter analysis, and perform experimental Type II band-splitting analysis for magnetic-field estimates from noise-reduced data.
- Plot one or more light curves on top of the dynamic spectrum by entering a frequency or clicking directly on the plot, with configurable color, width, opacity, labels, and line style.
- Combine frequency bands with improved gap-filling and overlap-handling options before importing the merged spectrum.
- Keep polygon, line, and text annotations inside the accelerated view, with editable text styling and project persistence.
- Save and reuse processing presets, optionally choose a default preset for future FITS loads, reopen restored analysis sessions, and run batch processing for folder-based FIT/FITS exports.

### Solar-event context tools
- Open standalone viewers for GOES X-ray flux, GOES SEP proton flux, SOHO/LASCO CME catalog data, Kyoto Dst, and GFZ Kp.
- Overlay GOES XRS curves directly on the main spectrum with automatic legacy/modern GOES fallback and flare-class guides.
- Analyze SDO/AIA images from **Analysis -> Solar Data Analysis** with crop, difference, active-region, composite, and movie export tools.
- Explore external archives with the SunPy Multi-Mission Explorer for SDO, SOHO, STEREO-A, and GOES products.
- Sync the current analyzer time window across supported solar-event windows for faster cross-comparison.

### Reproducibility and support
- Save full analyzer state as `.efaproj` project files and recover recent autosave snapshots.
- Export processed FITS files, provenance reports (Markdown + JSON), and analysis logs (CSV + TXT).
- Generate diagnostics ZIP bundles for bug reports and open a prefilled GitHub issue draft from inside the app.
- Use the built-in citation dialog to copy the recommended citation or BibTeX entry, and check for newer GitHub releases from the app.

---

## 📘 User Guide

This guide explains how to use the main features of the **e-CALLISTO FITS Analyzer**, including dynamic spectrum visualization, live noise reduction, annotations, burst isolation, drift estimation, maximum intensity extraction, best-fit analysis, Type II band-splitting analysis, FITS export, the e-CALLISTO and Learmonth radio downloaders, and the built-in CME, GOES, SEP, Dst, Kp, and SunPy modules.

---

# 1. Main Interface

After launching the application, the main window opens with tools for loading FITS files, adjusting thresholds, selecting colormaps, isolating bursts, navigating the spectrum, and performing scientific analysis.

The main functions are available through a compact **icon toolbar** for quick access and a clean layout.

### **Main Window**
![Main Window](assets/screenshots/main_window.png)

---

# 2. Loading a FITS File

You can load:

- **Compressed FITS:** `*.fit.gz`, `*.fits.gz`
- **Uncompressed FITS:** `*.fit`, `*.fits`

This supports observers who work directly with uncompressed raw data.

Choose **File → Open** or click the **Open** icon on the toolbar.  
The dynamic spectrum appears immediately.

---

# 3. Noise Reduction (Live Threshold Scrollbars)

Noise reduction updates **live** without pressing Apply.

Features:

- High-resolution lower and upper clipping sliders for smoother Vmin / Vmax adjustment
- Live threshold readouts next to each slider for quick feedback while dragging
- Optional **Logarithmic Threshold Scale** checkbox for finer control near zero
- Robust per-channel background subtraction for both single-band and frequency-combined plots
- **Processing → Presets → Raw FITS Percentile (5-98%)** sets noise-clipping limits from the current raw data distribution
- Saved processing presets can be applied manually or selected as the default preset for future FITS loads
- Dynamic spectrum refreshes automatically
- No data are lost when switching x-axis units (seconds ↔ UT)

### Example: Noise Reduction
![Noise Reduction](assets/screenshots/noise_reduction.png)

### RFI Cleaning Toolkit (Processing → RFI Cleaning)

RFI cleaning applies a deterministic pipeline to 2D dynamic spectrum data (**frequency × time**).  
Use **Preview** to inspect results first, then **Apply** to commit.

Processing steps:

1. 2D median smoothing with **Kernel (freq) × Kernel (time)**.
2. Hot-channel detection using a robust Z-score from each channel's level + variability.
3. Masked-channel repair by replacing flagged channels from neighboring channels.
4. Per-channel upper percentile clipping (high outliers only; low side preserved).

Parameter guide:

- **Kernel (time)**: median window along time. Higher values suppress short spikes more, but can blur fast burst structure.
- **Kernel (freq)**: median window across frequency channels. Higher values reduce narrow-band striping, but can widen spectral features.
- **Channel Z threshold**: robust outlier cutoff for hot-channel masking. Lower values mask more channels; higher values mask fewer.
- **Percentile clip**: upper cap per channel. Lower values clip peaks more aggressively; higher values preserve strong peaks.
- **Masked channels**: count/list of detected hot channel indices shown in the panel after Preview/Apply.

Suggested tuning workflow:

1. Start with defaults: `kernel(time)=3`, `kernel(freq)=3`, `Channel Z threshold=6.0`, `percentile clip=99.5`.
2. If channel streaks remain, lower **Channel Z threshold** gradually (for example `6.0 → 5.0 → 4.0`).
3. If burst detail looks over-smoothed, reduce kernel sizes and/or raise **Channel Z threshold**.
4. Use **Preview** repeatedly, then **Apply** when satisfied.
5. Use **Reset** in the RFI panel to restore default RFI settings. Use **Edit → Reset to Raw** to fully revert applied data.

### Example: RFI Cleaning
![RFI Cleaning](assets/screenshots/RFI_cleaning.png)

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
- bone_r

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
- **View → Set Display Range...:** enter exact start/stop times and frequency bounds for aligned station comparisons
- **View → Save/Apply Display Range Preset...:** reuse the same visible window on later files
- **View → Export/Import View Config...:** share display range, units, thresholds, colormap, and graph styling as `.efaview.json`
- **View → Multi-Station Comparison...:** compare several station spectra in vertically stacked panels with shared time/frequency axes, UT or seconds alignment, shared/per-station/manual color scaling, automatic time/frequency-combined views for combinable FITS selections, Modern-mode hardware rendering, Classic-mode Matplotlib rendering, and visible-view export as PNG/PDF/EPS/SVG/TIFF
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

### Light-Curve Overlays

Use **Analysis → Plot Light Curves** to overlay intensity-time curves on the dynamic spectrum.

Options:

- Enter a frequency manually
- Click directly on the spectrum to choose a frequency
- Use single-curve or multi-curve mode
- Customize curve color, thickness, opacity, vertical scale, line style, and frequency labels
- Clear all active light curves without resetting the loaded dataset

Light-curve overlays are preserved in project state and can be included in generated project reports.

---

# 9. Burst Isolation (Lasso Tool)

Click **Isolate Burst** and draw around the emission region.  
Only the selected region is retained for further analysis. In v2.6.0, the lasso mask is calculated against the rendered image pixel centers, so the isolated region follows the drawn path more accurately on the displayed spectrum.

### Example: Isolated Burst
![Isolated Burst](assets/screenshots/burst_isolation.png)

---

# 10. Maximum Intensities Extraction

Use **Analysis → Maximum Intensities → Open Maximum Intensities** to compute the maximum frequency for each time channel after noise reduction or burst isolation.

### Example: Maximum Intensities
![Maximum Intensities](assets/screenshots/maximum_intensity.png)

---

# 11. Outlier Removal

Inside the Maximum Intensities window:

- Draw a lasso to select outliers
- Remove them instantly
- Keep manual cleanup controls available even when automatic outlier removal is enabled
- Prepare the cleaned curve for fitting

---

# 12. Burst Analyzer (Best Fit & Shock Parameters)

The Analyzer window performs:

- Power-law fitting of the Type II backbone
- Drift-rate evaluation
- Shock speed
- Shock height
- R² and RMSE

Calculation updates in v2.6.0:

- Harmonic Type II shock calculations convert observed harmonic frequency/drift values to their fundamental equivalents before computing shock parameters
- Saved analysis summaries retain both the converted calculation values and observed-frequency reference fields
- Drift summaries are computed from valid, time-ordered segments and ignore zero-duration point pairs

Newkirk model option:

- **Newkirk fold number** can be selected as:
  - **1, 2, 3, 4**

Optional additional plots:

- Shock speed vs height
- Shock speed vs frequency
- Height vs frequency

### Example: Analyzer
![Analyzer](assets/screenshots/analysis.png)

Export options:

- Best-fit graph (PNG, PDF, EPS, SVG, TIFF)
- Data summary to Excel
- Multiple additional plots

---

# 13. Type II Band-Splitting Analyzer

Use **Analysis → Type II Band-splitting → Open Type II Band-splitting** to analyze split bands directly from the current noise-reduced dynamic spectrum.

Workflow:

- Add arbitrary points along the upper band
- Switch to the lower band and add points there
- Fit both bands with power-law curves
- Plot magnetic field versus shock height after successful calculation
- Calculate:
  - Shock speed
  - Shock height
  - Bandwidth
  - Compression ratio
  - Alfven Mach number
  - Alfven speed
  - Magnetic field

Important validation note:

- The Type II band-splitting magnetic-field workflow is still under development and should be treated as experimental.
- Derived magnetic-field values may not be accurate for all events or assumptions.
- Confirm results against already known or independently validated event data before using them for scientific conclusions.

---

# 14. Radio Data Downloaders

Open radio download tools via **Solar Events → Radio Bursts**.

### e-CALLISTO Downloader

Open via **Solar Events → Radio Bursts → e-CALLISTO**.

Features:

- Select station, date, and hour
- Fetch available files from the server
- Preview selected files
- Download multiple FITS files
- Import selected FITS files directly into the Analyzer
- Automatic detection of frequency or time stitching compatibility
- Clear error messages when selected files cannot be combined
- Generate a station's full UTC-day spectral overview from the **Spectral Overview** tab
- Generate separate preview tabs for every focus code available for the selected station/date, or regenerate one selected code
- Apply a day-wide median_dB background baseline and export the organized six-panel overview

### Example: Downloader
![Downloader](assets/screenshots/callisto_downloader.png)

![Downloader](assets/screenshots/callisto_downloader_preview.png)

### Learmonth Station Downloader

Open via **Solar Events → Radio Bursts → Learmonth**.

Features:

- Load or reuse cached Learmonth Station daily archive files for a selected date
- Inspect available Learmonth data chunks and their time ranges
- Download the raw Learmonth daily archive file when needed
- Convert selected Learmonth chunks into FIT files
- Import converted Learmonth FIT files directly into the Analyzer for the same noise reduction, visualization, and analysis workflow used by e-CALLISTO data

---

# 15. Combine FITS Files

Two combination modes are supported.

### **Combine Frequency**
Merge frequency bands with matching time bases. Frequency combining now has improved gap filling and overlap handling before the combined result is imported. When selected files contain a frequency gap or overlap, the app prompts for combine options. Gaps can be filled with interpolated background, average edge background, zeros, or a gray-hatched blank region. Overlapping bands can be split at a connection frequency, kept from the low band, kept from the high band, or rejected. The selected gap/overlap handling is retained in the combined dataset metadata where applicable.

### **Combine Time**
Merge consecutive time segments from the same station and date.

If files do not meet the required criteria, a message box alerts the user.

Combined data can be imported directly into the Analyzer.

---

# 16. Save and Reopen Analysis Projects

You can save the full analysis state to a project file and restore it later.

Path:

- **File → Save Project**
- **File → Save Project As...**
- **File → Open Project...**

Project format:

- **e-CALLISTO Project:** `*.efaproj`

Saved state includes plot view, thresholds, units, colormap, graph properties, loaded/combined data, and analysis-session state.

---

# 17. Export Data as FITS

You can now export processed data as a new FITS file with a modified header. This is useful for downstream analysis and **Machine Learning** workflows.

Export options:

- **Raw view**
- **Background-subtracted view**
- **Combined datasets** (time/frequency) with compatibility-preserving metadata updates

Path:

- **File → Export As → Export to FIT**

---

# 18. Saving and Exporting Plots

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

Batch plot exports are available from **Processing → Batch Processing**. Background subtraction options include per-channel mean, per-channel median, and **median_dB**, which applies the `2500 / 255 / 25.4` digit-to-dB scale before median background removal and defaults to the median_dB `-1` to `8 dB` display range. Enable **Use current display range** or load a saved `.efaview.json` config to export multiple station spectra with identical time/frequency axes.

For visual station-to-station comparison, open **View → Multi-Station Comparison...**. Add multiple FITS files, choose UT-clock or seconds-from-file-start alignment, select shared/per-station/manual color scaling, set a shared display range, and export the visible comparison view as PNG, PDF, EPS, SVG, or TIFF. If the selected files are time- or frequency-combinable, the workspace renders combined views automatically; mixed-station selections are combined per station before comparison. The comparison workspace follows the app mode: Modern uses hardware-accelerated panels when available, while Classic uses Matplotlib.

### Provenance and Analysis Logs

For reproducibility and audit trails, the app can export two structured report types from **File → Export As**:

- **Export Provenance Report...** writes both Markdown and JSON summaries of the loaded data source, processing settings, RFI configuration, annotations, time-sync state, and operation log.
- **Export Analysis Log...** writes CSV and plain-text summaries of analyzer fit parameters and derived shock metrics.

These reports are useful for lab notebooks, collaboration handoffs, and paper preparation.

### Project Report PDF

Use **File → Generate Project Report...** to create a consolidated PDF for the current project or loaded dataset.

The report can include:

- Raw dynamic spectrum
- Background-subtracted dynamic spectrum
- Light curves with the dynamic spectrum
- Maximum-intensity fit
- Type II band-splitting output
- Available GOES X-ray, GOES SGPS proton flux, Dst, and Kp context plots

The obsolete **Burst Isolated Dynamic Spectrum** report section has been removed in v2.6.0.

---

# 19. CME Catalog Viewer (SOHO/LASCO)

Features:

- Retrieve daily CME lists
- Display CME parameters in a structured table
- Show associated LASCO movies
- Event metadata panel

### Example: CME Viewer
![CME Viewer](assets/screenshots/cme_catalog.png)

---

# 20. GOES X-Ray Flux Viewer and Overlay

Features:

- Open a standalone GOES X-ray viewer for time-window inspection and flare analysis
- Select from historical and modern GOES XRS spacecraft directly in the standalone viewer
- Enable **Solar Events → GOES Overlay → Long(XRS-B)** and/or **Short(XRS-A)** to draw GOES curves on the current FITS spectrum
- Automatically fall back across date-appropriate **legacy and modern GOES XRS satellites** when loading overlay archives
- Display GOES overlay curves with a dedicated right-side flare-class guide (**A / B / C / M / X**) without modifying the spectrogram data
- Adjust time windows, extract flare parameters, and export plots/data from the standalone GOES viewer

### Example: GOES X-Ray Viewer
![GOES X-Ray](assets/screenshots/goes_xray.png)

---

# 21. GOES SEP Proton Flux Viewer

Path:

- **Solar Events → Energetic Particles → GOES SEP Proton Flux**

Features:

- Plot GOES SEP proton flux from the NOAA SGPS archive across multi-day UTC ranges
- Automatically try **GOES-19** through **GOES-16**, with manual spacecraft override available in the window
- Show proton channels closest to about **10 MeV** and **100 MeV**, along with hover readouts and selection-based event metrics
- Export both the plotted figure and the stitched flux table as PNG/CSV

### Geomagnetic Indices (Dst and Kp)

Path:

- **Solar Events → Geomagnetic → Kyoto Dst Index**
- **Solar Events → Geomagnetic → GFZ Kp Index**

Features:

- Fetch geomagnetic activity data across custom UTC ranges directly inside the desktop app.
- Visualize Kyoto Dst and GFZ Kp with dedicated storm-level guides for quick contextual interpretation.
- Export both plots and tabular data as PNG/CSV for reporting and comparison with solar-radio observations.

---

# 22. Solar Data Analysis

Path:

- **Analysis -> Solar Data Analysis**

Features:

- Search and download SDO/AIA image records using the existing SunPy cache workflow
- Load local AIA `.fit`, `.fits`, `.fit.gz`, and `.fits.gz` files
- Plot image sequences with frame stepping, playback, running-difference, and base-difference modes
- Crop image sequences using the plot-window ROI selector
- Detect bright active-region candidates and export centroid/bounding-box/intensity summaries as CSV
- Optionally fetch NOAA/HEK active-region labels and overlay them on detected regions
- Create simple RGB composites from loaded AIA frames
- Export the current plot, cropped FITS products, animated GIFs, and MP4 movies

Notes:

- JSOC server-side cutout requests are not part of v2.7.0; cropping is performed locally after files are loaded.
- Metadata overlays require network access, but image-based region detection works on local files.

---

# 23. SunPy Multi-Mission Explorer

Path:

- **Solar Events → Archives → SunPy Multi-Mission Explorer**

Supported v1 instruments:

- **SDO/AIA** (map products)
- **SOHO/LASCO C2/C3** (map products)
- **STEREO-A/EUVI** (map products)
- **GOES/XRS** (time-series products)

Features:

- Search SunPy archives using spacecraft/instrument/time filters
- Download selected records into an app-managed cache
- Plot map products with frame stepping and running-difference mode
- Compute ROI image statistics (min/max/mean/median/std/P95/P99)
- Plot GOES/XRS channels and derive basic flare summary metrics
- Export plots and analysis summaries

Known limitations:

- Requires network access for archive search/download (cached files can be reopened offline)
- JSOC/HMI workflows are not part of v1

---

# 24. Support and Research Tools

### Report a Bug

Use **About → Report a Bug...** to open the in-app diagnostics workflow.

- Capture session details, environment information, and user notes in a single report dialog.
- Generate a diagnostics ZIP bundle that can include structured provenance summaries for easier troubleshooting.
- Open a prefilled GitHub issue draft or copy the issue text before submitting.

### Cite this Software

Use the **Cite this Software** button in the main window to open the citation dialog.

- Copy the recommended citation text for the e-CALLISTO FITS Analyzer paper.
- Copy the BibTeX entry directly into your manuscript or reference manager workflow.

### Check for Updates

Use **About → Check for Updates...** to query the latest release from GitHub.

- If a newer version is available, the app shows current/latest versions and a direct download action.
- Downloads run in-app and save the installer/package to your selected location.
- If you are up to date, the app confirms your current version.
- If the check fails (for example, no network), the app shows a clear error message.

---

## 🛠️ Build and Packaging

### Prerequisites
- Python 3.12 is recommended for Windows local development and packaging.
- Python 3.12+ is recommended for macOS local development and packaging.
- Linux packaging scripts can also work with Python 3.11+ when required by the target system.

### Run from Source
- Create and activate a virtual environment.
- Install dependencies:
  - `python src/Installation/install_requirements.py`
- Start the app:
  - `python src/UI/main.py`
- On Windows source runs, plotting imports are prepared before the splash appears. The first run after a dependency change may briefly print `Preparing plotting runtime...`; allow it to finish.
- Windows: if `PySide6.QtCore` fails with `ImportError: DLL load failed`, repair the venv and reinstall the pinned runtime stack:
  - `powershell -ExecutionPolicy Bypass -File .\src\Installation\repair_windows_venv.ps1`
  - `.\venv\Scripts\python.exe src\UI\main.py`
- The Windows repair script requires Python 3.12 by default and will not silently fall back to Python 3.14 or another installed version. To explicitly use another tested version, pass `-PythonVersion`.
- Start the standalone Kyoto Dst index plotter:
  - `python src/UI/dst_index_gui.py`
- Start the standalone GFZ Kp index plotter:
  - `python src/UI/kp_index_gui.py`
- Start the standalone GOES SEP proton flux plotter:
  - `python src/UI/goes_sgps_gui.py`

### Build dependencies
- Install runtime dependencies:
  - `python src/Installation/install_requirements.py`
- Install build tooling:
  - `python -m pip install pyinstaller pyinstaller-hooks-contrib`
  - macOS only: `python -m pip install py2app`

### Windows (PyInstaller + optional Inno Setup installer)
- Recommended scripted build:
  - `powershell -ExecutionPolicy Bypass -File .\src\Installation\build_windows_installer.ps1`
- Optional app-folder-only build:
  - `powershell -ExecutionPolicy Bypass -File .\src\Installation\build_windows_installer.ps1 -SkipInstaller`
- Manual installer script:
  - `src/Installation/FITS_Analyzer_InnoSetup.iss`

### Linux (.deb + PyInstaller)
- Debian/Ubuntu build prerequisites:
  - `sudo apt-get update`
  - `sudo apt-get install -y python3-venv python3-pip`
- Use Python `3.11+` for packaging. If you already have a working project venv, the script will prefer it automatically.
- If your machine uses a stale pip mirror, point the build at official PyPI:
  - `PIP_INDEX_URL=https://pypi.org/simple bash src/Installation/build_deb_linux.sh`
- If you need a specific interpreter:
  - `PYTHON_BIN=/usr/bin/python3.13 bash src/Installation/build_deb_linux.sh`
  - `PYTHON_BIN="$(pwd)/.venv/bin/python" PIP_INDEX_URL=https://pypi.org/simple bash src/Installation/build_deb_linux.sh`
- Recommended `.deb` packaging workflow:
  - `bash src/Installation/build_deb_linux.sh`
- Install the generated local package using a path, not a bare filename:
  - `sudo apt install -y ./dist/e-callisto-fits-analyzer_2.7.0_amd64.deb`
  - If you are already inside `dist`, use `sudo apt install -y ./e-callisto-fits-analyzer_2.7.0_amd64.deb`
- Manual PyInstaller build:
  - `pyinstaller src/Installation/FITS_Analyzer_linux.spec`

### macOS (py2app)
- Build app bundle:
  - `python src/Installation/setup.py py2app`

### Generic cross-platform spec
- Alternative build entry:
  - `pyinstaller src/Installation/FITS_Analyzer.spec`

---

## 📄 Notes

- Supports `.fit`, `.fits`, `.fit.gz`, and `.fits.gz`
- Project save/load format: `.efaproj`
- Live noise reduction with preserved zoom, pan, and axis format
- Cursor-based data readout for time, frequency, and intensity
- Provenance/report export and diagnostics bundles are available from the desktop UI
- Improved plotting area for clearer scientific visualization
- Robust export system with OS-aware save handling
- Major plots are publication ready
- Linux fallback for problematic GPU stacks: `CALLISTO_FORCE_SOFTWARE_OPENGL=1`
- Linux Qt platform fallback: Wayland is used by default when the desktop session provides it. If a specific Ubuntu/Wayland setup has Qt input issues, try `CALLISTO_PREFER_QT_XCB=1`; explicit `QT_QPA_PLATFORM` values are respected.

---

## ⭐ Credits

Developed by **Sahan S. Liyanage**  
Astronomical and Space Science Unit  
University of Colombo, Sri Lanka
