# e-CALLISTO FITS Analyzer v3.1.0 — Windows

This Windows beta introduces multi-view GCS CME fitting and improves background subtraction for solar radio spectra. It also adds staged fit refinement and expands the figure, movie and report exports available from the GCS fitting window.

## What's new

### GCS CME and shock fitting

- **Multi-view reconstruction:** open **Analysis → GCS CME Fitting…** to fit a shared Graduated Cylindrical Shell to CME images from SOHO/LASCO and STEREO. Synchronized views, running/base differences, playback and focus layouts help compare the front across instruments.
- **Separate shock fitting:** fit the outer shock with a spheroid or ellipsoid alongside the GCS flux rope. Each model maintains its own front points, recorded fits and height–time measurements.
- **Staged refinement:** **Refine fit** (`Ctrl+R`) starts with two front points to refine height. Additional points and suitable viewpoint separation progressively enable direction and shape parameters. The status bar identifies the fitted parameters and the points needed for the next stage.
- **Recorded fits and kinematics:** commit fits through the event sequence, revisit recorded models and display their shells during playback. Fit height–time measurements with linear, quadratic or cubic models and inspect the resulting speeds, accelerations and available uncertainties.

### GCS exports

- Save viewpoint snapshots and height–time graphs as **PNG, PDF, EPS, SVG, TIFF or JPG** with a white background.
- Export **GIF or MP4 movies** with the recorded model overlays. MP4 requires FFmpeg; GIF is offered when it is unavailable.
- Generate a **PDF fitting report** with viewpoints, observation details, recorded models, height–time fits, kinematic comparisons and method references. Polynomial fits are included where supported by the measurements.
- Export recorded measurements as **CSV** and model parameters, front points and observation provenance as **JSON**.

### Background subtraction

- Choose **Mean**, **Median** or **Median (dB)** from the dedicated **Background Subtraction** section.
- Reapply subtraction directly from the raw data, so changing methods replaces the previous correction without stacking it.
- Adjust the **Noise Clipping Thresholds** independently for live contrast control. These sliders change the displayed color range without clipping the data.

## Fixes and improvements

- GCS snapshots are rendered from the underlying data, addressing blank image panels in hardware-accelerated exports.
- Movie and fitting-report exports restore the displayed time, working models, refinement results and front points when finished.
- Height–time graph titles identify the model and selected fit, and fitting reports provide clearer parameter and kinematic comparisons.
- The in-app guide now explains the GCS/shock workflow, staged refinement, recording and export options.

## Windows installation

**Installer:** `e-CALLISTO_FITS_Analyzer_v3.1.0_Setup.exe`

1. Download the Windows installer from the release assets.
2. Close any running instance of e-CALLISTO FITS Analyzer.
3. Run the installer and approve the Windows administrator prompt.
4. Follow the setup wizard, then launch the application from the Start menu or the optional desktop shortcut.

The installer targets Windows systems capable of running x64 applications.

## Beta feedback

This is a prerelease of v3.1.0. Please report issues through the application's **Report a Bug** tool, including the steps to reproduce the problem and a diagnostics bundle when available.

GCS and shock results depend on front selection, viewing geometry and image timing. Reported fit uncertainties do not include every source of reconstruction uncertainty.
