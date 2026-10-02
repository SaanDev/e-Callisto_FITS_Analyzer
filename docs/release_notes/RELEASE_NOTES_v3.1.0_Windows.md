# e-CALLISTO FITS Analyzer v3.1.0 — Windows

v3.1.0 adds automatic ridge tracking and a choice of coronal density models to radio burst analysis, multi-view GCS CME and shock fitting, PFSS coronal magnetic field modelling in Solar Image Analysis, and publication-style figure exports. Files and projects can now be dropped onto the window or reopened from recent lists, and the e-CALLISTO downloader lists the stations that actually observed on the chosen day.

## What's new

### Radio burst analysis

- **Automatic ridge tracking:** **Track Ridge** in the Maximum Intensities window follows the burst's peak frequency column by column instead of taking the brightest channel at every time. It starts from the brightest point of the burst, or from a point chosen with **Pick Start**, bridges short dropouts and refines each peak to sub-channel frequency. Channels that stay bright for most of the file are treated as RFI. **Edit → Restore Per-Column Maxima** brings back the original points, and **Analyze → Ridge Tracking Settings…** sets the search window, threshold, allowed gap and a drift-to-lower-frequency-only option.
- **Selectable power-law time origin (t₀):** the Analyzer fits f = a·(t − t₀)^−b with t₀ set to **File start** (the default and previous behaviour), **Burst onset** or a **Custom time** in UT.
- **Coronal density models:** shock heights and speeds can use **Newkirk** (default, unchanged results), **Saito**, **Leblanc**, **Baumbach–Allen** or **Mann**, each with the 1–4 fold multiplier. A comparison table lists the shock speed and height from all five models. The chosen model and t₀ are saved in projects, named in the project report and added to the Excel export.
- **Type II band-splitting** now opens with the main window's noise clipping thresholds, so its spectrum matches the noise-reduced plot it was opened from.

### Opening files

- **Drag and drop:** drop FITS files or an `.efaproj` project anywhere on the main window. Several FITS files are combined exactly as with **File → Open**.
- **Recent Files and Recent Projects:** reopen the last ten FITS selections or projects from the **File** menu. A combined set is remembered as one entry and combined again when reopened.

### GCS CME and shock fitting

- **Multi-view reconstruction:** open **Analysis → GCS CME Fitting…** to fit a shared Graduated Cylindrical Shell to synchronized SOHO/LASCO and STEREO images, with running/base differences, playback and focus layouts.
- **Separate shock fitting:** fit the outer shock with a spheroid or ellipsoid alongside the GCS flux rope. Each model keeps its own front points, recorded fits and height–time measurements.
- **Staged refinement:** **Refine fit** (`Ctrl+R`) starts with two front points to refine height; more points and suitable viewpoint separation progressively enable direction and shape parameters. The status bar names the fitted parameters and the points needed for the next stage.
- **Exports:** viewpoint snapshots and height–time graphs (PNG, PDF, EPS, SVG, TIFF, JPG), GIF/MP4 movies with recorded model overlays, a PDF fitting report, CSV measurements and JSON model parameters with observation provenance. MP4 requires FFmpeg; GIF is offered when it is unavailable.

### Solar Image Analysis

- **PFSS coronal magnetic field modelling:** extrapolate the coronal field from a GONG, HMI, GONG ADAPT or local synoptic magnetogram and overlay open and closed field lines and coronal-hole boundaries on the AIA disk. **Diagnostics…** shows the input magnetogram, the source-surface field with its neutral line, the open/closed footpoint map and the solution statistics. Solves run in the background, and downloads are cached.
- **Circle Fit** now records the CME height as 1 R☉ plus the fitted circle's diameter (h = 1 R☉ + 2r), modelling the CME as an expanding sphere resting on the solar surface. Sessions saved by earlier versions still open.

### Figure exports

- **Export Figure**, every graph in the **Project Report**, Maximum Intensities **Export As**, the Analyzer's **Save Graph** and Type II **Save Plot** now produce OriginPro-style graphs on a white page — Arial type, a closed frame with inward ticks on all four sides and a framed colour scale — whichever renderer and theme are on screen. JPG joins PNG, PDF, EPS, SVG and TIFF. On-screen plots keep their look, except the Analyzer's **Best Fit** graph, which is now drawn in the same style.

### Background subtraction

- Choose **Mean**, **Median** or **Median (dB)** from the dedicated **Background Subtraction** section. Reapplying subtraction starts from the raw data, so changing methods replaces the previous correction instead of stacking it.
- The **Noise Clipping Thresholds** sliders adjust the displayed color range independently and do not clip the data.

### e-CALLISTO downloader

- The station lists in the Single Station, Multi-Station Event and Spectral Overview tabs are read from the archive for the selected UTC day, replacing the fixed station list. Station names that change over time are shown as the archive records them for that day. Lookups are cached.

## Fixes and improvements

- Fixed SDO/AIA level 1.5 in the packaged application, which reported that `aiapy` was missing even though it was bundled.
- Opening Solar Image Analysis no longer loads the PFSS modelling stack, so the window opens noticeably faster. If PFSS is unavailable, the card is disabled and explains why.
- Updated the Qt runtime from PySide6 6.10.1 to 6.11.2.
- The GCS CME Fitting window title shows its version.
- GCS snapshots are rendered from the underlying data, fixing blank image panels in hardware-accelerated exports. Movie and fitting-report exports restore the displayed time, working models, refinement results and front points when finished.

## Windows installation

**Installer:** `e-CALLISTO_FITS_Analyzer_v3.1.0_Setup.exe`

1. Download the installer from the release assets.
2. Close any running instance of e-CALLISTO FITS Analyzer.
3. Run the installer and approve the Windows administrator prompt.
4. Follow the setup wizard, then launch the application from the Start menu or the optional desktop shortcut.

The installer upgrades an existing v3.x installation, including the v3.1.0 beta, in place. It requires 64-bit Windows 10 or 11.

## Notes

- GCS and shock results depend on front selection, viewing geometry and image timing. Reported fit uncertainties do not include every source of reconstruction uncertainty.
- PFSS models are computed from synoptic magnetograms assembled over a full solar rotation, so they describe the global field around an observation rather than the instantaneous field.
- Please report problems through **About → Report a Bug...**, including the steps to reproduce them and a diagnostics bundle when available.
