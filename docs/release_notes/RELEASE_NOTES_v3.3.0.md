# e-CALLISTO FITS Analyzer v3.3.0

v3.3.0 measures features on the solar surface. Solar Image Analysis, now v1.8, measures filament lengths and the areas of sunspots and post flare arcades, corrected for the curvature of the Sun, records them in a table and follows them through a sequence. The e-CALLISTO downloader can fetch FITS files from the Astrodoncel server as well as from e-CALLISTO. Windows on ARM and Linux arm64 get native installers, for the full app and for Lite.

Lite is released with it as **e-CALLISTO FITS Analyzer Lite v3.3.0-lite**, under the same `v3.3.0` tag.

v3.3.0 opens every v3.2.0 project. Projects saved before v3.3.0 reopen with their solar frames unrotated, as they were saved.

## What's new

### Lengths and areas on the solar surface

Two new tools sit on the Solar Image Analysis toolbar beside Pan and Zoom, with a new **Surface** tab next to CME Tracking. They work on any on disk image with a WCS: SDO/AIA and HMI, SOHO/EIT, STEREO/EUVI and GOES/SUVI.

- **Length**: click along the feature and double click (or press **Finish**) at its end. The line is deprojected onto the sphere as great circle arcs. Any part beyond the limb is measured in the plane of the sky and reported on its own.
- **Area**: draw a polygon (click the corners), a freehand lasso, an ellipse or a rectangle. Pixels on the disk are corrected for foreshortening and reported in millionths of the solar hemisphere (MSH) and in km². Pixels off the limb are given as an uncorrected plane of sky area.
- **Undo Point**, **Finish** and **Clear Shape** edit the shape being drawn. `Esc` or a right click cancels it.

Inside an area you can measure the whole region, or only the pixels that pass a threshold:

- **Sunspot only** separates umbra and penumbra (by default below 0.6 and 0.9 of the quiet Sun). This is the standard intensity threshold method. A patch counts only if it is a real depression, with its darkest pixel at least 0.10 below the penumbra level, so granulation is not counted. Dark thresholds ignore the sky and the outermost limb.
- **Darker than** and **Brighter than** take a level of your own.
- Intensities are divided by a limb darkening profile of the quiet Sun fitted over the whole disk. The threshold always reads the frame's own data, even while a difference image is shown.
- **Min patch** drops isolated noise pixels, and **Tint pixels** shows which pixels were selected.

The area method is yours to choose. **Finite distance** geometry (the default) follows each pixel's line of sight from the spacecraft; **Orthographic** is there for comparison with older catalogues. A **Limb cutoff** (μ ≥ 0.30 by default) leaves out pixels too close to the limb and tints them red, and boundaries can be counted at **Pixel centres** or with **Subpixel 4×4** sampling. The result card gives the status (valid, partial, invalid or a valid zero), the pixels left out and why, and a sensitivity range for a mask grown or shrunk by 1 px and a threshold moved by 0.02.

The **Values** selector shows results, the table and the graph either corrected for curvature (the true size on the Sun) or as seen on the image. The CSV export always holds both.

**Record** adds a measurement to the table, and rows that share a name are one feature over time. **Track Frames** carries the shape across the sequence, optionally turned with the Sun (**Rotate shapes with the Sun when tracking**, using Howard's synoptic differential rotation). A graph plots the feature's length or area over time with a linear rate. The **Table** menu exports a CSV, or a JSON file with full provenance. Shapes, records and settings are saved in the `.efaproj` project.

Sunspot areas read about 1.4 times the NOAA/USAF region areas, which are known to be too low (Meadows 2020, MNRAS 497, 1110). No scaling factor is applied.

### Checked geometry

- The solar radius comes from the header's RSUN_REF and DSUN_OBS and is cross checked with RSUN_OBS. A measurement is refused when they disagree.
- Every loaded frame's pixel size is checked against its image size (1024², 2048² or 4096²). For images with a sharp limb (HMI continuum, AIA 1700 and 4500) the limb is fitted from the pixels and compared with the header, and a stale header left over from 2× or 4× binning is corrected. The verdict is shown in the result card and written to the export.

### North Up

Frames stored rotated, such as raw HMI at about 180°, are now turned north up when they load. The image, the NOAA and HEK labels, the coordinate grid and the latitude and longitude under the cursor then agree. **North Up** is on by default and is in the display options and on the toolbar. Projects saved before v3.3.0 reopen unrotated.

### Choice of FITS server in the downloader

- A **Server** selector above the e-CALLISTO downloader's tabs chooses where every tab finds and downloads FITS files: **e-CALLISTO** (the default) or **Astrodoncel**. The choice is remembered. Changing it keeps your dates and time windows, clears the previous server's results and reloads the station lists.
- When the selected server fails, the error names it and a **Retry** button appears. The app never switches servers by itself. An open preview names the server its files came from.
- Files of the same name from the two servers are cached apart (Astrodoncel's under `sources/astrodoncel`). Every new download is checked to be a complete, readable FITS file before it is cached, and extending an imported observation's timeline continues on the server it came from.
- Lite has the same selector, and takes over the full app's choice when it imports the full app's settings.

### Native ARM64 builds for Windows and Linux

- Windows 11 PCs with an ARM processor get installers of their own for the full app and Lite, which run natively instead of under x64 emulation. The ARM64 installer installs over an x64 copy of the same edition, keeping its folder and settings.
- In the full app on Windows on ARM, CME movies from the SOHO/LASCO CME catalog open in your web browser instead of the app's movie window, because PySide6 for Windows on ARM does not include Qt WebEngine. Everything else is the same as on x64.
- Linux arm64 machines (an ARM server, a Raspberry Pi with a 64 bit OS, or a virtual machine on an Apple silicon Mac) get `.deb` packages for both editions. The full app's arm64 package needs Ubuntu 24.04 or later, because PySide6 (the app's Qt library) ships Linux ARM64 builds only for glibc 2.39, Ubuntu 24.04's, and newer.
- The update check now knows which processor it runs on. On Windows and Linux an x64 copy is offered the x64 installer and an ARM64 copy the ARM64 installer, in both the full app and Lite. A Windows on ARM PC that runs the x64 build under emulation keeps being offered x64 installers; install the ARM64 one once by hand to switch. Macs are Apple silicon only, so nothing changes there.

## Fixes

- The ruler, CME heights, lengths and areas on cropped frames, and on headers without RSUN_OBS (common in STEREO and GONG crops), now use the solar radius derived from the observer's distance. They used a fixed 960″ before, which made lengths 4 to 8% and areas 9 to 26% too large.
- Measurements on rotated frames now take the image rotation (CROTA or the PC matrix) into account, so centroids and latitudes and longitudes no longer land in the mirrored hemisphere.
- PFSS open field boundaries no longer vanish on AIA frames, whose solar sphere (RSUN_REF) is slightly larger than 1 R☉.
- The details drawer under the solar image can be dragged taller, and maximising and restoring the window no longer leaves it unable to shrink.
- Exported figures are saved in the format you chose. The default file name comes from the plot title, which often holds a FITS file name, and a name such as `…_02.fit-Background Subtracted` looked to the save dialog as if it already had an extension, so the export failed with an unsupported format. The name in the dialog now ends in the chosen format, and a name that ends in anything else gets that format's extension added. This covers Export Figure, the Event Figure, the Geomagnetic Response figure and Lite's figure exports.

## Downloads

Each version is published under a single tag (`v3.3.0`) that carries every installer for both the full app and Lite. Choose the file for your system:

| Platform | Full application | Lite |
|---|---|---|
| Windows 10 / 11 (x64) | `e-CALLISTO_FITS_Analyzer_v3.3.0_Setup.exe` | `e-CALLISTO_FITS_Analyzer_v3.3.0-lite_Setup.exe` |
| Windows 11 on ARM (ARM64) | `e-CALLISTO_FITS_Analyzer_v3.3.0_arm64_Setup.exe` | `e-CALLISTO_FITS_Analyzer_v3.3.0-lite_arm64_Setup.exe` |
| macOS 13 Ventura or later (Apple silicon) | `e-callisto-fits-analyzer_3.3.0_macOS_arm64.dmg` | `e-callisto-fits-analyzer_3.3.0-lite_macOS_arm64.dmg` |
| Linux x64 (Debian, Ubuntu and derivatives) | `e-callisto-fits-analyzer_3.3.0_amd64.deb` | `e-callisto-fits-analyzer_3.3.0-lite_amd64.deb` |
| Linux arm64 (Ubuntu 24.04 or later) | `e-callisto-fits-analyzer_3.3.0_arm64.deb` | `e-callisto-fits-analyzer_3.3.0-lite_arm64.deb` |

Installers with `-lite` in their names are for Lite; the others are the full application. Each installer upgrades an existing installation of the same edition in place. Downloads and help are on [ecallistoanalyzer.org](https://ecallistoanalyzer.org/).

Please report problems through **About → Report a Bug...**, with the steps to reproduce them and a diagnostics bundle when available.
