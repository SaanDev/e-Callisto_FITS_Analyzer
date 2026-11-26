# e-CALLISTO FITS Analyzer v3.2.0

v3.2.0 turns a radio burst into the story of a whole event. One project file now holds the radio analysis, Solar Image Analysis and GCS fitting. The new Geomagnetic Response follows the event to Earth, an event strip and an event figure put the flare, CME and storm catalogs next to the spectrum, and the project report opens with the event's story. Solar Image Analysis gains fast JPEG2000 quicklooks and reduced-size FITS, honest download progress, and the AIA Sun inside LASCO's occulter.

This is also the first release of **e-CALLISTO FITS Analyzer Lite**, and the first to ship every platform under a single tag.

**Projects saved by v3.2.0 cannot be opened by v3.1.** Keep a copy of any project you still need to open in v3.1 before saving it again in v3.2.0.

## Official website

**[ecallistoanalyzer.org](https://ecallistoanalyzer.org/)** is now the home of the project:

- Downloads for Windows, macOS and Linux
- The illustrated [user guide](https://ecallistoanalyzer.org/guide/)
- [Tutorials](https://ecallistoanalyzer.org/tutorials/)
- [How to cite the software](https://ecallistoanalyzer.org/citation/)

In the app, **Help → Official Website** opens it, and **About** shows the address. The Windows installer and the Linux package also list it as the publisher and support site.

## What's new

### One project for every window

- **A single `.efaproj` file** holds the radio spectrum and its analysis, the Solar Image Analysis frames, view and measurements, and the GCS CME fits. A project may hold any of these on its own. **Open Project** opens and restores every window the project needs, and `Ctrl+S` (`⌘S` on macOS) in the main, Solar Image Analysis or GCS window saves the same project.
- **Link or embed:** when a project holds solar or GCS images, **Save Project As** asks whether to **Link data (small)**, referring to the image files on this computer, or **Embed data (portable)**, storing them in the project (the dialog shows the size) so it opens anywhere, even offline. A linked project whose frames have been moved or cleared from the cache downloads them again.
- **Reliable reopening:** a part of a project that cannot be read is skipped and named while the rest opens. Solar measurements are matched to the reloaded frames by observation time. A built overlay composite is built again once the frames are in.
- **Older files:** v3.1 radio projects open unchanged. A v3.1 `.ecsolar` Solar Image Analysis session opens as a new, unsaved project; `.ecsolar` files are no longer written.

### Geomagnetic Response

- **Solar Events → Geomagnetic → Geomagnetic Response…** shows what Earth's environment did around the event, from one day before to seven days after (the span is editable), on one UTC axis: Kyoto Dst with the 1-minute SYM-H, GFZ Kp coloured by NOAA G level, the IMF (Bt, Bz, By), solar-wind speed, density, temperature and pressure, Ey, β, Mach and AE/AL/AU.
- Solar wind comes from OMNI 5-minute data, time-shifted to the bow shock. Where OMNI is not yet published, DSCOVR or SWPC real-time L1 data stand in and are labelled *L1, not time-shifted to the bow shock*. DONKI shock arrivals at Earth, storms and their linked CMEs are marked, and the radio interval is shaded.
- A **Geomagnetic Response** sidebar card summarises the Dst minimum (and how many hours after the event it came), the Kp maximum and its G level, the lowest Bz, the fastest solar wind and the storm class.
- The event is the loaded data's start, the row selected in the burst list, or the day or file selected in the e-CALLISTO downloader; **Use loaded data** goes back to the loaded file. Export the figure in the OriginPro style or a CSV whose provenance column names the archive or spacecraft of every value. The response is saved in the project.

### Event Context Strip and Event Figure

- **View → Event Context Strip** adds lanes above the spectrum, in both renderers: GOES flares (the NOAA SWPC list), CDAW LASCO CMEs, NASA DONKI CMEs, flares, shocks, storms and SEP events, and e-CALLISTO bursts. Hover a marker for details; events just outside the plot are counted at the lane's edge. **View → Event Context Sources** chooses the lanes. Catalogs are cached per day; a day an archive cannot answer for yet (a CDAW month not yet published) is asked for again, and its lane is marked (!).
- **File → Export As → Event Figure…** composes one publication figure of the whole event: image thumbnails (a frame already loaded in Solar Image Analysis or the GCS window, otherwise a Helioviewer image), the spectrum, SWAVES, GOES X-rays and protons, the catalog lanes and the geomagnetic block, at 8.8 cm, 12 cm, 18 cm or a custom width, with a live preview. **Fetch missing data** downloads what the chosen panels need.
- **Solar Events → Fetch Event Context Automatically** (on by default) fetches the geomagnetic response and catalogs when data is loaded or an event is selected. Turn it off to download nothing on your behalf until you press **Refresh**, **Refresh Event Context** or generate a report.

### Project report

- The report opens with an **Event Story**: a headline and a chain (flare → radio burst → CME → shock at Earth → storm), the event-chain graphic, key numbers, four short chapters, a timeline of every milestone, the event overview and geomagnetic response figures, and the sources and caveats. Pieces are matched by timing and by DONKI's links, and the story says when they do not fit: a storm that peaked before this event's shock arrived is attributed to earlier eruptions.
- New **Solar Image Analysis** (current frame, instrument, frames, processing, overlay layers, CME kinematics) and **GCS CME Fitting** (viewpoints and recorded fits) sections.
- Projects without a radio spectrum get a report too.
- Catalogs and the geomagnetic response the report fetches are kept by the sidebar card, the strip and the project.

### Solar Image Analysis

- **Download products:** a **Product** box chooses *FITS · full resolution*, *FITS · reduced size* or *JP2 · quicklook*, and remembers the choice. Reduced size is for SDO: the AIA *Synoptic 1024²* archive needs no e-mail, and JSOC's binned and cutout frames need the JSOC notify e-mail. JP2 quicklooks come from Helioviewer for AIA, HMI, EIT, LASCO and STEREO EUVI/COR1/COR2 — listed in seconds and a few hundred kilobytes a frame. JP2 pixels are display values, not calibrated data, so processing levels, light curves, Region Stats and active-region detection are disabled for them, and the frames are labelled so.
- **Download progress** follows the real transfer on every source (bytes, speed and time remaining, with per-file details), shows a busy bar with the reason while nothing can be measured, and reaches 100% only when the frames are on screen. **Stop** also cancels the wait for a JSOC export.
- **AIA 193 Sun in the occulter** (Coronagraph Tools, LASCO C2 and C3): one tick fills each frame's occulted centre with the nearest SDO/AIA 193 Å image, the whole image with its corona, at true scale. Composites are now blended live on every draw, so the colormap, contrast, scale, NRGF filter and running or base difference keep working on them, and an **AIA colormap** recolours the AIA image at once.
- **Movies** play north up, as on screen, with an optional stripe naming the instrument, UTC time and difference mode on every frame.

### Also new

- **Reset All** returns the app to how it starts. With unsaved changes it asks to Save, Discard or Cancel.

## Introducing e-CALLISTO FITS Analyzer Lite

Lite is the radio part of the e-CALLISTO FITS Analyzer, rewritten in Rust as a small, fast application with the same user interface as the full app.

- **Everything for e-CALLISTO radio data:** opening and combining FITS files, the timeline, background subtraction, RFI cleaning and noise clipping, light curves, drift rates, annotations, maximum intensities, burst analysis and Type II band-splitting, the e-CALLISTO and Learmonth downloaders, the burst list, multi-station comparison, batch processing, figure export and the project report.
- **Shares projects with the full app:** Lite opens full-app `.efaproj` projects and saves projects the full app opens.
- **Runs on old and low-end PCs:** it draws with the GPU when there is one and falls back to software drawing on old drivers, remote desktops and virtual machines.
- **Installs side by side** with the full app, with its own install folder, shortcuts and settings. On its first start it takes over the full app's theme, presets and recent files.
- **Official Website** is in Lite's Help menu too.

## One release for every platform

From v3.2.0, each version is published under a single tag (`v3.2.0`) that carries the installers for every platform, for both the full app and Lite. Choose the file for your system:

| Platform | Full application | Lite |
|---|---|---|
| Windows 10 / 11 (64-bit) | `e-CALLISTO_FITS_Analyzer_v3.2.0_Setup.exe` | `e-CALLISTO_FITS_Analyzer_v3.2.0-lite_Setup.exe` |
| macOS 13 Ventura or later (Apple silicon) | `e-callisto-fits-analyzer_3.2.0_macOS_arm64.dmg` | `e-callisto-fits-analyzer_3.2.0-lite_macOS_arm64.dmg` |
| Linux (Debian, Ubuntu and derivatives, amd64) | `e-callisto-fits-analyzer_3.2.0_amd64.deb` | `e-callisto-fits-analyzer_3.2.0-lite_amd64.deb` |

Installers with `-lite` in their names are for Lite; the others are the full application. The full app's installers upgrade an existing v3.x installation in place, and the in-app update check offers each edition its own installer. Downloads and help are on [ecallistoanalyzer.org](https://ecallistoanalyzer.org/).

Please report problems through **About → Report a Bug...**, including the steps to reproduce them and a diagnostics bundle when available.
