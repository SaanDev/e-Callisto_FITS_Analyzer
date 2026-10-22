# e-CALLISTO FITS Analyzer Lite — Rust plan

A radio-only edition of the e-CALLISTO FITS Analyzer, written in Rust, with the
same user interface as the full app (v3.2.0), including all of its icons.

## 1. Decisions

| Topic | Decision |
|---|---|
| Name and version | **e-CALLISTO FITS Analyzer Lite**, versioned with the full app and marked `-lite` (3.2.0-lite), installable side by side with it |
| Platforms | Windows, macOS and Linux; every phase is built and tested on all three |
| Plot canvas | One canvas with the **Modern** look; the *View → Mode* menu is removed. Exported figures still match the full app's exports |
| Project files | **Two-way**: Lite opens full-app `.efaproj` projects and saves projects the full app opens. Parts Lite does not handle are kept unchanged |
| Settings | Lite keeps its own settings and offers to import the full app's theme, presets and recent files on first start |
| Old or low-end PCs | Must work: GPU drawing when available, software drawing otherwise (old drivers, remote desktop, virtual machines) |
| Code location | `lite/` in this repository, sharing `assets/` and the user-guide text with the full app |
| Releases | From 3.2.0 one tag (`v3.2.0`; pre-releases `v3.3.0-beta`) carries the full app's three installers and Lite's three, always both. Lite is first released in v3.2.0 once every feature in scope is done; you review a build at the end of each phase |

**What "same UI" means here.** The reference is the full app v3.2.0 on each
platform, in the light and dark themes. For every window and dialog, a Lite
screenshot taken next to the full-app screenshot in the same state must match
in layout, sizes, spacing, colours, icons, texts, and menu and toolbar order.
Small differences in text anti-aliasing and in how individual widgets are drawn
are accepted, because Lite cannot use Qt to draw them.

## 2. Scope

### Kept: everything for e-CALLISTO radio data

The Python files named here are the reference for behaviour and for results.

| Area | Features | Python reference |
|---|---|---|
| Opening data | Open, drag and drop, Recent Files, *Combine FITS Files…* in time, frequency and time × frequency with gap fill and overlap options; `.fit`/`.fits`/`.gz`; ARTEMIS-IV `ARTLOOK`; Learmonth `.srs` | `backend/radio/burst_processor.py`, `fits_io.py`, `artemis.py`, `learmonth.py`, `frequency_axis.py`, `ui/radio/dialogs/combine_dialogs.py` |
| Timeline | Previous/Next (×1–8), prefetch, Trim Start/End, local files first, day-listing cache | `backend/radio/callisto_timeline.py`, `callisto_cache.py` |
| Processing | Background subtraction (Mean, Median, Median dB, noise equalisation), RFI cleaning (panel, apply, reset), noise clipping thresholds with log scale, units (digits/dB, seconds/UT), linear/log frequency axis, display range and its presets, Undo/Redo, Reset to Raw, Reset All | `noise_reduction.py`, `rfi_filters.py`, `batch_processing.py`, `backend/common/array_stats.py`, `display_range_dialog.py`, `rfi_control_dialog.py` |
| Plot | Modern canvas: wheel zoom, rectangular zoom, lock/unlock, cursor readout in the status bar, colorbar, colormaps, Graph Properties (colormap, font family, titles, font sizes) | `ui/widgets/accelerated_plot_widget.py`, `measurement_readout.py` |
| Measurement and analysis | Light curves (enter or click a frequency, settings, clear), ruler, drift-rate estimate, isolate burst, annotations (polygon, line, text; edit, move, toggle, delete, clear), maximum intensities with ridge tracking and outlier auto-clean, *Analyze Burst* (power-law fit, t₀ options, five density models with fold multiplier, shock parameters, comparison table, Excel export), Type II band-splitting and its graph settings, Analysis Summary section | `ridge_tracking.py`, `density_models.py`, `type_ii_band_splitting.py`, `ui/radio/dialogs/analyze_dialog.py`, `max_intensity_dialog.py`, `type_ii_band_splitting_dialog.py`, `light_curve_*_dialog.py`, `annotation_text_dialog.py`, `backend/session/annotations.py`, `measurements.py` |
| Data tools | e-CALLISTO downloader (stations per day, files grouped by focus code, previews that combine, download queue), Learmonth downloader, burst list with previews, multi-station comparison and its export, batch processing | `ui/downloads/callisto_downloader.py`, `archive_stations.py`, `download_queue_panel.py`, `learmonth_downloader.py`, `backend/radio/burst_list.py`, `ui/radio/burst_list_tab.py`, `burst_preview.py`, `multi_station_comparison*.py`, `comparison_export_dialog.py`, `batch_processing_dialog.py`, `spectral_overview.py` |
| Projects and outputs | Open/Save/Save As project, Recent Projects, Recover Last Session, Analysis Session, presets (save, apply, default, delete), view config import/export, Export Figure (PNG, JPEG, TIFF, SVG, PDF, EPS), Export to FIT, project report (PDF), provenance report, analysis log | `backend/session/project_session.py`, `ui/app/project_parts.py`, `recovery_manager.py`, `analysis_session.py`, `presets.py`, `view_config.py`, `backend/common/figure_export.py`, `backend/radio/radio_figures.py`, `project_report.py`, `ui/app/report_story.py`, `provenance.py`, `analysis_log.py` |
| App | Themes (System, Light, Dark), collapsible sidebar with remembered state, startup splash, FITS header viewer, user guide, citation, bug report, update check, About | `ui/common/theme_manager.py`, `font_utils.py`, `ui/widgets/collapsible_sections.py`, `ui/app/startup_loading.py`, `ui/radio/fits_header_viewer.py`, `ui/help/*`, `backend/services/update_checker.py`, `bug_report.py` |

The main window (`ui/app/main_window.py`, 15.5k lines) and `ui/app/gui_workers.py`
wire all of these together and are ported piece by piece across the phases.

**Main window inventory to reproduce**

- **Menus:** File, Edit, Download, Solar Events, View, Analysis, Processing, About, Help.
- **Toolbar (16 buttons, icons from `assets/icons` and `assets/icons_dark`):** Open, Download, Export, Export as FITS, Save Project, Undo, Redo, Estimate Drift Rate, Isolate Burst, Plot Maximum Intensities, Rectangular Zooming, Lock, Unlock, Reset Selection, Reset to Raw, Reset All, plus *Cite this Software* on the right.
- **Sidebar sections:** Timeline, Background Subtraction, Noise Clipping Thresholds, Units, Axis, Graph Properties, Analysis Summary. Their expand/collapse arrows are the two `chevron_*_small.svg` icons.
- **Other icons:** `assets/band_splitting_icons/{light,dark}` for the Type II window, and `assets/branding` for the app icon.

### Removed

| Removed | Where it appears in the full app |
|---|---|
| STEREO/SWAVES | *Solar Events → Radio Bursts → SWAVES*, the SWAVES panel toggle, the SWAVES panel under the spectrum, SWAVES in figures and projects |
| Other solar events | *Solar Events →* CMEs, Flares, Energetic Particles, Geomagnetic, Archives; *Sync Current Time Window*; GOES Overlay |
| Event context | *View → Event Context Strip* and *Event Context Sources*; the geomagnetic card |
| Solar images and CMEs | *Analysis → Solar Image Analysis*, *GCS CME Fitting…* |
| Event Figure | *File → Export → Event Figure…*: all of its panels except the spectrum are removed features |
| Renderer choice | *View → Mode* (one canvas) and *Processing → Hardware Acceleration* (always on) |

*Solar Events → Radio Bursts → e-CALLISTO / Learmonth* stays, so the menu bar
keeps the same layout as the full app.

## 3. Architecture

```
lite/
  Cargo.toml              workspace
  crates/
    callisto-core/        no GUI: FITS, combining, processing, analysis,
                          projects, figures, reports, settings import
    callisto-lite/        the application (egui)
  parity/                 Python scripts that produce reference results
                          with the full app's own functions
  tools/                  generators: colormap tables, user guide, theme tokens
  PLAN.md
```

Keeping all non-GUI code in `callisto-core` lets it be tested headless and
compared directly with the Python results.

| Part | Approach |
|---|---|
| Starting point | The prototype in `Desktop/CALLISTO Rust` (FITS reader, joining, rendering, archive listing), moved into `callisto-core` and extended |
| GUI toolkit | eframe/egui with a custom theme. Colours, corner radii (12 px cards), paddings and fonts come from the palettes and stylesheet in `theme_manager.py` |
| Icons | The same SVG files from `assets/`, rendered with resvg at the full app's sizes and switched with the theme |
| Fonts | The same families the full app picks in `font_utils.py` (Segoe UI, Helvetica Neue, DejaVu Sans), loaded from the system. Installed fonts are listed with `fontdb` for the Graph Properties font picker |
| Drawing | wgpu (DirectX 12, Metal, Vulkan). If no usable GPU is found, fall back to a software adapter (WARP on Windows, llvmpipe/lavapipe on Linux). Phase 0 checks this on a remote-desktop session and a virtual machine. If it fails there, use egui's OpenGL backend with a bundled Mesa `opengl32.dll`, the same thing Qt's `opengl32sw.dll` is (about +20 MB) |
| Plot canvas | Own widget that renders only the visible region at screen resolution (prototype `render.rs`). Axes, ticks, colorbar and the log-axis decade ticks follow the Modern canvas and `frequency_axis.py` |
| Colormaps | A tool exports every colormap the full app offers from matplotlib as a 256-entry table, so colours are identical |
| Figure export | One figure description is written as SVG with the full app's figure style (`mpl_style.py`). resvg turns it into PNG, JPEG and TIFF, svg2pdf into PDF, and a small custom writer produces EPS |
| Reports | krilla for PDF output, plus a simple layout for headings, paragraphs, tables and images, matching `project_report.py` |
| Excel | rust_xlsxwriter, with the same sheets and columns as the full app's Analyze export (including the density-model and t₀ columns) |
| Fitting | levenberg-marquardt (nalgebra), with 1σ errors from the fit covariance, as scipy `curve_fit` gives |
| Projects | zip + `meta.json` + `arrays.npz` (serde_json, ndarray-npy). Lite reads and writes the `radio` part. The other parts (`solar`, `gcs`, `geomagnetic`, `event_context`, `composer`) and embedded files are copied unchanged, so work saved in the full app survives a save from Lite |
| Settings import | Reads the full app's QSettings for `SaanDev` / `e-CALLISTO FITS Analyzer` (registry on Windows, plist on macOS, INI on Linux): theme, presets (JSON), recent files and projects (JSON lists), sidebar section states |
| Background work | Loading, combining, downloading and fitting run on worker threads, as `gui_workers.py` does, so the window never freezes |
| Network | ureq for the archive, burst list, Learmonth and update check |
| Update check | The shared tags; each app takes the installers named for it (Lite's carry `-lite`). The full app's `update_checker.py` skips `-lite` installers (and `lite-` tags); copies up to 3.1.x cannot, and take the first installer GitHub lists, so the full app's are uploaded first (`packaging/check_release_assets.py` checks the published release) |
| Installers | Inno Setup on Windows (reusing `packaging/windows`), a signed and notarised `.dmg` on macOS, `.deb` on Linux; different install folders and app IDs from the full app |

## 4. Checking that Lite matches the full app

**Results.** `lite/parity/make_reference.py` runs the full app's own Python
functions on a fixed set of real files and saves their outputs. Rust tests load
the same files and compare, within float32 tolerance, or within the reported
uncertainty for fits:

- loading, all combine modes, gap fill and overlap options
- background subtraction, RFI cleaning, quantiles, dB conversion, log-axis resampling
- light curves, maximum intensities and ridge tracking
- power-law fit with each t₀ option, the five density models, shock parameters
- band-splitting fits

The files come from `Desktop/New Data`: a full INDIA-GAURI day (96 files), a
100-channel station, a time × focus-code grid, and several other stations.
There are no ARTEMIS-IV or Learmonth samples on this machine yet, so a few are
downloaded in Phase 0. A local run over all 4,443 files in `New Data` checks
that every file the full app opens also opens in Lite.

**User interface.** Each phase adds paired screenshots: the full app (real
window, light and dark) and Lite in the same state (egui_kittest), shown side
by side for review. A checklist built from the full app's menus, toolbar,
sidebar and dialogs is ticked off item by item. The full app's plot canvas does
not paint in Qt's offscreen mode, so its reference screenshots are taken from
the real window.

**Projects.** Round-trip tests: full app → Lite → full app keeps every part
intact, including `Desktop/H-Alpha/AIA_20241101_0000.efaproj`, which has solar
parts.

**Figures.** Lite and full-app exports of the same view are compared side by side.

**Speed targets** (on this 28-thread PC; a 4-core laptop is measured in Phase 0):

| Task | Full app today | Lite target |
|---|---|---|
| Open a full day (96 files) | 14.2 s | under 0.5 s |
| Open a 4-file event | 104 ms | under 50 ms |
| Redraw after a threshold change, full day | about 80 ms | under 20 ms |
| RFI clean, full day | 4.6 s | under 0.5 s |
| Start-up | 1.8 s warm, 6.9 s cold for the Python imports alone | under 1 s to a usable window (Lite not yet measured) |

**CI.** The repository has no CI yet. A GitHub Actions workflow builds and tests
Lite on Windows, macOS and Linux and runs the parity tests against the Python
code in the same checkout.

## 5. Phases

Size is the amount of full-app Python code each phase ports, not counting its
share of the main window (15.5k lines) and workers (0.9k lines), which spread
across all phases.

| Phase | Ports | Python lines |
|---|---|---|
| 0 Foundations | setup, risk checks | – |
| 1 Main window and data | canvas, loading, combining, processing, timeline | about 10,600 |
| 2 Analysis | light curves to band-splitting | about 5,500 |
| 3 Data tools | downloaders, burst list, comparison, batch | about 10,400 |
| 4 Projects and outputs | projects, figures, reports | about 5,000 |
| 5 Shell and release | help, updates, installers | about 2,000 |

### Phase 0: foundations and risk checks
- Branch `analyzer-lite`; `lite/` workspace; prototype code moved in; CI on three platforms.
- Window drawing on a normal GPU, a remote-desktop session, a virtual machine, an old integrated GPU, macOS and Linux; software fallback chosen.
- Theme tokens from `theme_manager.py`, the icon pipeline and system fonts.
- Parity harness and fixture set, including the ARTEMIS-IV and Learmonth downloads.
- Full app: skip `lite-` tags in the update checker.
- **Exit:** Lite's empty main window (menus, toolbar, sidebar cards, status bar) matches the full app's in light and dark on all three systems. If this can't be met, the plan is revisited before any further work.

**Status (2026-10-15), on branch `analyzer-lite`:**

| Item | State |
|---|---|
| Workspace, prototype code moved into `callisto-core` (no GUI dependency) | Done |
| Theme tokens, icons, system fonts | Done |
| Empty main window, menus and submenus, Windows, light and dark | Done: 0.8 % of pixels differ, nearly all text anti-aliasing (Qt uses ClearType) and the sidebar scrollbar (Lite has no geomagnetic card). Menu popups have identical sizes. The first reference showed the canvas as a grey box, an artefact of grabbing an OpenGL view; references now render the canvas scene, which shows pyqtgraph's empty plot |
| Drawing: GPU, software adapter, OpenGL, automatic fallback | Done on this PC; the software adapter (WARP) draws pixel-identical to the GPU |
| Parity harness | Done for loading: 8 real files identical value by value; all 4,443 files in `Desktop/New Data` open in both apps |
| Full app ignores `lite-` releases in its update check | Done (`update_checker.py`, with a test); must ship in a full-app release before Lite 1.0 |
| CI on Windows, macOS, Linux | Workflow written (`.github/workflows/lite.yml`); not yet run on GitHub |
| Remote desktop, virtual machine, old integrated GPU | Not yet tested |
| macOS and Linux references and comparison | Not yet captured |
| ARTEMIS-IV and Learmonth fixtures | Not yet downloaded |

### Phase 1: main window and core data
- Every menu, toolbar button and sidebar control in place; controls for later phases show the same dialogs once those phases land.
- Modern canvas: zoom, pan, rectangular zoom, lock, cursor readout, colorbar, colormaps, Graph Properties, linear/log axis.
- Loading FITS, ARTEMIS-IV and Learmonth; all combine modes with their dialogs; drag and drop; Recent Files; FITS header viewer.
- Background subtraction, RFI panel, thresholds, units, display range and its presets, Undo/Redo, Reset to Raw, Reset All.
- Timeline section.
- **Exit:** parity tests pass for loading, combining and processing; the speed targets are met; main-window screenshots match.

**Status (2026-10-15), on branch `analyzer-lite`:**

| Item | State |
|---|---|
| Modern canvas | Done. pyqtgraph's layout, axes (spacing, labels, SI prefix, crowding, text-space history), colour bar, title and image are ported; pan, wheel and right-drag zoom, single-axis zoom on the axes, rectangle zoom, lock and the cursor readout work as in the full app. Ticks equal pyqtgraph's in every captured state; lines and image edges match pixel for pixel; text sits within 0.3 px |
| Main window with data: empty, one file, combined, background, dB, UT, log axis; light and dark | 0.8–1.1 % of pixels differ (ClearType text, kerning, and the geomagnetic card Lite does not have) |
| Loading, all combine modes with the frequency-options dialog, drag and drop, Recent Files | Done; parity 147/147 |
| File → Combine FITS Files... | Done. Its preview is drawn by Lite's canvas; the matplotlib look comes with Phase 4's figures |
| Background subtraction, thresholds (linear and log sliders), units, axis scale, Graph Properties (including font family), Undo/Redo, Reset Selection/to Raw/All | Done |
| RFI panel, FITS header viewer, Set Display Range and its presets | Done; their dialogs are not yet compared with captured references |
| Timeline: Prev/Next through local files or the archive (with Lite's own download cache), Trim, availability probe, prefetch, undo | Done |
| Learmonth | Loads through the FITS loader; not checked until sample files arrive |
| macOS and Linux | Not yet built or compared |

Speed on this PC (release build, INDIA-GAURI 2026-10-07, 96 files):

| Task | Target | Lite |
|---|---|---|
| Open a full day | under 0.5 s | 0.35 s |
| Open a 4-file event | under 50 ms | 16 ms |
| Redraw after a threshold change, full day | under 20 ms | 3.5 ms to build the image (plus its upload) |
| RFI clean, full day | under 0.5 s | 0.44 s |
| Start-up to the first complete frame | under 1 s | 0.6 s |

Found in the full app while porting: for images with NaN or gap rows,
pyqtgraph applies the colour levels to the RGBA bytes a second time (alpha
included), so after background subtraction gaps turn semi-transparent grey and
colours shift. Lite reproduces it for now; fixing it in both is a decision for you.

### Phase 2: analysis
- Light curves, ruler, drift rate, isolate burst, annotations.
- Maximum intensities with ridge tracking and auto-clean.
- Analyze Burst with fits, t₀, density models, shock parameters and Excel export.
- Type II band-splitting and its graph settings; Analysis Summary section.
- **Exit:** parity tests pass for every analysis result; every analysis dialog matches.

**Status (2026-10-20), on `master`: every item done.**

| Item | State |
|---|---|
| Light curves, ruler, drift rate, isolate burst, annotations | Done. Main window in 13 tool states, light and dark: 1.0–1.4 % of pixels differ |
| Maximum Intensities with ridge tracking, auto-clean and export | Done. Window 0.8–1.1 % |
| Analyze Burst: fits, t₀, density models, shock parameters, Excel export | Done. Light theme 0.7–1.5 %, dark 1.5–2.6 % (light ClearType text on the dark panels) |
| Analysis figures (scatter, best fit, power-law labels in mathtext, legends) | Every axes, text extent and tick identical to matplotlib's (`figures_parity`) |
| Settings dialogs: ridge tracking, light-curve frequency and settings, text annotation, colour, choose label | 1.2–6.0 % (small, text-heavy windows) |
| Analysis Summary section | Done |
| Type II band-splitting window and its graph settings dialog | Done. Window in 5 states (empty, points, fits, results, B versus R), light and dark: 1.0–1.4 % of pixels differ; every detail label's rich text identical to the full app's; graph settings 1.8–1.9 %. Save Plot draws both figures with matplotlib's constrained layout, in the settings' font family and bold/italic (Phase 4) |

Found in the full app while porting (Lite reproduces them; fixing them in
both apps is a decision for you):

- With the light theme on a dark operating system, the Analyzer's and Type II's
  scrolled panels are dark: `QScrollArea` fills its viewport with the system
  palette's window colour.
- The Analyzer's comparison table clips its header texts.
- The Type II title keeps ".fit" from a `.fit.gz` name (`os.path.splitext`
  removes only ".gz").
- Type II: a band with no points comes back from the shared session as one
  NaN point (the session stores the empty array as `None`, and
  `np.asarray(None)` is a single NaN). So a fresh window shows "Selected
  points 1" for each band, Undo and Clear are enabled with nothing to undo,
  and Fit Both Bands is enabled after one click on each band.
- Type II: before any calculation the averaging interval reads "[, ]". The
  session keeps the empty results as a record of `None`s, which the window
  takes for results. Changing the shock-speed source then says "Shock speed
  source changed. Recalculate plasma parameters." although nothing was
  calculated.
- Type II: when Fit Both Bands fits the upper band and then fails on the
  lower one (reachable through the NaN point above), the upper fit is kept
  but no label or plot is updated until the next point or fit.

### Phase 3: data tools
- e-CALLISTO downloader with station lookup, focus-code grouping, previews and download queue.
- Learmonth downloader; burst list and previews.
- Multi-station comparison and its export dialog; batch processing.
- **Exit:** the same downloads and comparisons as the full app for a set of test days; dialogs match.

**Status (2026-10-16), on branch `analyzer-lite`.** The download and import
part was brought forward, ahead of Phase 2:

| Item | State |
|---|---|
| e-CALLISTO downloader: Single Station, Multi-Station Event, Spectral Overview, Burst List tabs, cache footer (open, clear) | Done. Same messages, statuses and worker behaviour; station lists per day with the full app's cache and retry rules |
| Previews (single and combined panels), spectral overview, burst FITS search | Done; figures are matplotlib's own layout ported to Rust |
| Learmonth downloader (daily `.srs`, 15-minute chunks, Convert to FIT, Convert and Import) | Done; tested on a synthetic day until real samples arrive |
| Import into the main window ("Importing FITS Files" progress, single file, combined, frequency options) | Done |
| Compare (multi-station comparison) | Done (see below): the prepared files open in the comparison window |
| Figure export from the overview | PNG, TIFF and SVG as matplotlib writes them; PDF and EPS embed a raster image for now |
| Results against the full app | `downloads_parity` (listings, candidate filters, burst catalog, Learmonth chunks and FIT bytes) and `figures_parity` (every axes, text extent and tick of the preview and overview figures) pass |
| Looks against the full app, light and dark | Downloader tabs 0.4–1.7 % of pixels differ, previews 0.6–1.0 %, Learmonth 0.7–2.3 %, calendar and station popups 3–6 %, import progress 5 % (a 200 × 70 window, so its text weighs more). What remains is ClearType text against Lite's grey anti-aliasing; field edges, tables, tab bars and figure text sit on the same pixels |
| macOS and Linux | Not yet built or compared |

**Status (2026-10-20): batch processing and the multi-station comparison.**

| Item | State |
|---|---|
| Batch FIT Processing (Processing → Batch Processing) | Done. The same messages, summary and output files; a real run over three fixtures writes the full app's file names. Dialog in four states, light and dark: 1.4–2.1 % of pixels differ |
| Batch output PNGs | Same view, levels, colour bar and layout as matplotlib's (`figures_parity`, four modes). Pixels 1.6–4.5 % apart: at 300 dpi matplotlib's text boxes come from FreeType's autohinter (whole pixels, advances moved by stem snapping), which Lite approximates to a pixel or two, and the noisy spectra shift with them. Matching exactly would need FreeType itself |
| Figure export (all windows) | Now laid out again at the output dpi and cropped like Agg (canvas truncated to whole pixels, anchored bottom-left), as `savefig` does |
| Multi-Station Comparison (Processing menu, the downloader's Compare) | Done, in the Modern view's hardware panels: files, the largest valid combinations, UT or seconds alignment, units, colormaps (with *gray*), shared, per-station and manual scales, noise reduction for all panels or one, the ruler, a locked range (UT or seconds), view configs |
| Comparison results against the full app | Combinations, labels, aligned extents and colour levels identical in five captured states (`comparison_parity`) |
| Comparison looks, light and dark | Window in six states 0.7–2.1 %; status line, readout and panel list identical |
| Export Comparison dialog | 2.3–3.5 % |
| Grid export | Figure layout identical to matplotlib's in three layouts (`figures_parity`); the PNG 1.9 % apart |
| Visible-view export | 4.2 % apart: Lite captures each panel from the screen and resamples it to the exporter's width, where pyqtgraph draws it again at that width |
| macOS and Linux | Not yet built or compared |

Found in the full app while porting (Lite reproduces them; fixing them in
both apps is a decision for you):

- The app stylesheet sets `color` on labels, fields, check boxes and radio
  buttons without a `:disabled` rule, so disabled controls look enabled; only
  push buttons grey out. Examples: *Background Method* in batch Raw mode, the
  grid options of Export Comparison while *Visible View* is chosen.
- The batch dialog's Cancel button never reads "Cancel Run":
  `_set_controls_enabled(False)` runs before the worker thread exists.
- The comparison's Low/High spin boxes make its controls 508 px wide in a
  352 px view, so the controls scroll sideways and are cut off at the default
  splitter position.
- The comparison's scrolled controls and the gaps between its panels are dark
  in the light theme on a dark operating system (the scroll-area palette, as in
  the Analyzer).
- Editing one panel's noise reduction recomputes that panel's colour levels
  from its own data, also under *Shared scale*, until the next full redraw.
- In dB units the comparison uses each panel's lower clipping threshold as the
  cold digits, even when noise clipping is not the method: after trying
  clipping at −10, dB values stay offset by 10 digits.

Found earlier in the full app while porting:

- The event window's *Start (UTC)* and *Stop (UTC)* edits are local-time
  `QDateTimeEdit`s. They start at local time and are then searched as UTC, which
  is 5.5 h off on this PC. Lite uses UTC.
- With the light theme on a dark operating system, Fusion's palette follows the
  system. Preview figures, the preview tab bar and the calendar popup come out
  dark. In the calendar the day numbers are drawn in the theme's dark text
  colour on that dark base and are nearly invisible. Lite reproduces both for
  now; fixing them in both apps is a decision for you.

### Phase 4: projects and outputs
- `.efaproj` read and write, two-way with pass-through; recovery; Analysis Session; presets; view config.
- Figure export in all six formats; Export to FIT; project report; provenance report; analysis log.
- **Exit:** round-trip tests pass; exported figures and reports match the full app's side by side.

**Status (2026-10-21), on `master`:**

| Item | State |
|---|---|
| `.efaproj` read and write (zip, `meta.json`, `arrays.npz`, embedded files, atomic save, schema 1 and 2) | Done. `project_parity`: Lite opens five projects the full app wrote (raw, processed, analysis, combined, schema-1 legacy) and saves them; meta key for key and every array (dtype, shape, values) equal what the full app writes back itself. The full app's reader opens Lite's files, and their `meta.json` is byte-identical to `json.dumps` (`parity/check_lite_projects.py`) |
| Parts Lite does not have (solar, GCS, geomagnetic, event context, composer) | Kept and written back unchanged: sections, arrays, embedded files. They keep the storage (link or embed) they were opened with |
| Open, Save, Save As, Recent Projects, drag and drop, "Unsaved Project" on open and close | Done; dirty marking at the full app's 20 places |
| Main window after opening a project | 1.1 % of pixels differ, as for the other states |
| Recovery (autosave every 3 minutes while unsaved, unclean-exit prompt, Recover Last Session) | Done; snapshots in Lite's own data folder, the newest ten kept |
| Presets (save, apply, default on load, delete) | Done. The preset of the processed state, and that preset applied to the raw project and saved, equal the full app's |
| Import and Export View Config | Done; the exported file equals the full app's (`json.dumps` layout) |
| Analysis Session → Open Restored Analysis | Done |
| Export Analysis Log (CSV + TXT) and Export Provenance Report (JSON + Markdown) | Done; equal the full app's but for the time, run id and app identity |
| Export to FIT | Done; byte-identical to the full app's file for a raw, a processed and a combined dataset |
| Export Figure (`dynamic_spectrum_figure`): colour bar, hatched gaps, UT or seconds axis, log frequency axis, light curves, annotations, the ruler, the on-screen zoom and levels, Graph Properties font and emphasis | Done. `figures_parity`: four graphs (plain; zoomed with gaps and every overlay; labels outside the axes; log axis with bold/italic text) laid out as matplotlib lays them out, to 0.01 px. `export_figure`: Lite's 300 dpi PNG of five projects against the full app's export: same size, 0.7–1.1 % of pixels differ (3.5 % on a log axis, where the image's aliased noise follows sub-pixel text-width differences) |
| Constrained layout, Save Graph and Save Plot | Done: margins are reset between the layout's two passes and `savefig` lays out once, as matplotlib does (both were wrong before and cancelled out for the earlier figures). Save Plot now uses the Type II settings' font family and emphasis |
| PDF and EPS as vector files | Done: paths, clipping and alpha (PDF; PostScript draws it opaque, as matplotlib's backend does), images at 300 dpi, text as glyph outlines (matplotlib embeds Type 42 fonts, so its text stays selectable). Pages equal the full app's tight box to 0.01 pt; the PDF replayed through matplotlib shows the PNG export's picture |
| Generate Project Report (PDF) | Done, without the Event Story (it needs the event context Lite does not have). ReportLab's layout is ported (paragraphs wrapped by words or characters, tables split between rows, KeepTogether, frames and page breaks; DejaVu Sans bundled, as the full app takes it from matplotlib). `report_parity`: Lite lays out the full app's report input exactly as ReportLab did (188 flowables per report, positions to 1e-6 pt, every line break). `project_report`: Lite builds the report of four projects from its own state; same pages, positions and lines as the full app's, figures within 3 px of its size and 1.1–2.3 % of pixels apart (8.4 % for a zoomed noisy spectrum, whose aliasing follows sub-pixel text-width differences). Progress dialog with Cancel, the full app's messages |

Found in the full app while porting (Lite reproduces them):

- An isolated burst's auto-cleaned maxima never restore their windows from a
  project: the session holds fewer points (1675) than the data has columns
  (3600), and `validate_session_for_source` reports "Analysis vector length
  does not match current dataset time-axis length."
- Opening a project leaves the Timeline section on the previous dataset's
  observations: `_apply_project_payload` never calls `_refresh_timeline_panel`.
- File → Save As in the main window is never connected and always disabled.
- Without an analysis session, the project report still draws a "Maximum Intensity Fit" graph (empty, x axis "Time channel") and a Type II figure without lanes, captioned "Maximum-intensity trace." and "Drawn from the Type II band-splitting view.": `np.asarray(None, dtype=float)` is one NaN, so the checks for data (`n == 0`, `_has_band_points`) pass.

### Phase 5: app shell and release
- User guide, generated from `ui/help/user_guide_content.py` without the chapters for removed features, plus citation, bug report, update check, About, startup splash, and first-start settings import.
- Installers for the three platforms, side by side with the full app.
- Full review of the checklist and parity results with you; Lite released in v3.2.0 (3.2.0-lite).

**Status (2026-10-21), on `master`:** everything but the review and the release.

| Item | State |
|---|---|
| User guide (Help → User Guide, F1) | Done. `tools/make_user_guide.py` writes `assets/user_guide.html` from the full guide: the solar imaging, PFSS, GCS, SWAVES, event context, geomagnetic response, event figure and SunPy chapters dropped, the rest renumbered, every sentence about removed features cut or reworded; each edit must apply exactly once, so a change to the full guide stops the script. Lite lays the HTML out as `QTextDocument` does: `help_windows` compares all 159 blocks with the full app's `QTextBrowser` showing Lite's guide (same positions, heights and line breaks; same document height). Home, Back, links, Find Next with wrap-around and "was not found.", selection and copy. Window 2.3 % of pixels apart, 6–8 % scrolled (text rendering) |
| Cite this Software | Done; 6.2 % (two text fields of text) |
| Report a Bug | Done: issue text, prefilled GitHub issue, diagnostics zip (`bug_report.json`/`.md`, issue notes, provenance JSON/Markdown) as `bug_report.py` writes them. The environment names egui where the full app names Python and Qt. 1.4–1.7 % |
| About | Done (`QMessageBox.about`: the application icon at 64 px). 5.7–5.9 % |
| Message boxes | Every Lite notice is now a `QMessageBox` laid out as Qt lays it out (`setupLayout`, `updateSize` with its 500 px soft limit, `adjustSize` for wrapped informative text, the platform's button order, Show/Hide Details). Sizes of eight captured boxes equal Qt's to 1 px (DirectWrite advances run a fraction of a pixel under egui's). Icons are drawn after Windows 11's: 2.8–5.9 % |
| Update check | Done: the shared release tags (`v3.2.0`; the old per-platform tags such as `v3.1.1(Windows)` are still read and have no Lite installer), Lite's `-lite` installers only (the full app now skips them), the status-bar label, the startup check 1.5 s after start (not with `CALLISTO_LITE_OFFLINE`), Update Available with release notes behind Show Details, Up to Date, Check Failed, the download with its progress dialog (3.7–6.3 %), Cancel, Update Downloaded with Open File / Open Folder |
| Startup splash | Done: the window opens as the 540 × 300 splash (the four loading steps), then becomes the maximised main window; 2.8 % apart. `CALLISTO_LITE_NO_SPLASH` skips it |
| First-start settings import | Done: with no Lite settings yet, Lite reads the full app's (registry, plist or INI) and takes over theme, sidebar sections, frequency axis scale, auto-clean, processing and display range presets, the default preset, light-curve settings and recent files and projects. The INI reader is checked against a file Qt wrote (`parity/make_settings_reference.py`); the registry and plist readers are not yet tried on real stores |
| Installers | Windows: `packaging/windows/build_windows_installer.ps1` (Inno Setup, after the full app's script): own AppId, "e-CALLISTO FITS Analyzer Lite" folder and Start menu group; the executable carries the icon and version. Built and checked here. macOS: `packaging/macos/build_macos_dmg.sh` (arm64, bundle id `…fitsanalyzer.lite`, Developer ID signing and notarisation when configured). Linux: `packaging/linux/build_deb_linux.sh` (package `e-callisto-fits-analyzer-lite`, its own command and desktop entry). Files: `e-CALLISTO_FITS_Analyzer_v3.2.0-lite_Setup.exe`, `e-callisto-fits-analyzer_3.2.0-lite_amd64.deb`, `e-callisto-fits-analyzer_3.2.0-lite_macOS_arm64.dmg`. The macOS and Linux scripts are not yet run; the Lite workflow builds all three on demand (Run workflow) |
| Version | 3.2.0-lite everywhere Lite shows it (title, About, guide, splash, installers); Windows and macOS version fields get 3.2.0 |

Found in the full app while porting (Lite reproduces it):

- The user guide's stylesheet sizes do not apply: `QTextBrowser` keeps its
  default sizes (body 12 px, `h1` 24 px, `h2` 18 px, single line spacing),
  so `build_default_stylesheet`'s 10.5 pt body, 20 pt and 15 pt headings and
  140 % line height never show. Only the class rules (`.lead` at 11 pt) and
  the colours take effect.

## 6. Risks

| Risk | Mitigation |
|---|---|
| egui cannot match the Qt look closely enough | Phase 0 exit criterion: the empty main window has to match before anything else is built |
| Exported figures differ from matplotlib's (tick placement, text layout, symbols like t₀ and R☉) | Largest single job in Phase 4; compared side by side for every figure type |
| Software drawing fails on remote desktop or virtual machines | Tested in Phase 0; Mesa fallback if needed |
| Numbers differ from the full app | Parity harness from Phase 0, run in CI on every change |
| Two codebases drift apart | Every radio change in the full app gets a matching Lite task; parity references are regenerated from the Python code in CI, so a change there fails the Lite tests until it is ported |
| No user feedback until 1.0 | You review a build at the end of every phase |
