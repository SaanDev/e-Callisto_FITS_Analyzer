# e-CALLISTO FITS Analyzer Lite — Rust plan

A radio-only edition of the e-CALLISTO FITS Analyzer, written in Rust, with the
same user interface as the full app (v3.2.0), including all of its icons.

## 1. Decisions

| Topic | Decision |
|---|---|
| Name and version | **e-CALLISTO FITS Analyzer Lite**, own versions starting at 1.0.0, installable side by side with the full app |
| Platforms | Windows, macOS and Linux; every phase is built and tested on all three |
| Plot canvas | One canvas with the **Modern** look; the *View → Mode* menu is removed. Exported figures still match the full app's exports |
| Project files | **Two-way**: Lite opens full-app `.efaproj` projects and saves projects the full app opens. Parts Lite does not handle are kept unchanged |
| Settings | Lite keeps its own settings and offers to import the full app's theme, presets and recent files on first start |
| Old or low-end PCs | Must work: GPU drawing when available, software drawing otherwise (old drivers, remote desktop, virtual machines) |
| Code location | `lite/` in this repository, sharing `assets/` and the user-guide text with the full app |
| Releases | One public release (1.0.0) when every feature in scope is done. You review a build at the end of each phase; nothing is published before 1.0 |

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
| Update check | GitHub releases tagged `lite-v*`. **The full app needs a small change too:** `update_checker.py` would read a `lite-v1.0.0` tag as version 1.0.0, so it must skip `lite-` tags. That change has to ship in a full-app release before Lite 1.0 |
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

### Phase 3: data tools
- e-CALLISTO downloader with station lookup, focus-code grouping, previews and download queue.
- Learmonth downloader; burst list and previews.
- Multi-station comparison and its export dialog; batch processing.
- **Exit:** the same downloads and comparisons as the full app for a set of test days; dialogs match.

### Phase 4: projects and outputs
- `.efaproj` read and write, two-way with pass-through; recovery; Analysis Session; presets; view config.
- Figure export in all six formats; Export to FIT; project report; provenance report; analysis log.
- **Exit:** round-trip tests pass; exported figures and reports match the full app's side by side.

### Phase 5: app shell and release
- User guide, generated from `ui/help/user_guide_content.py` without the chapters for removed features, plus citation, bug report, update check, About, startup splash, and first-start settings import.
- Installers for the three platforms, side by side with the full app.
- Full review of the checklist and parity results with you; Lite 1.0.0 released.

## 6. Risks

| Risk | Mitigation |
|---|---|
| egui cannot match the Qt look closely enough | Phase 0 exit criterion: the empty main window has to match before anything else is built |
| Exported figures differ from matplotlib's (tick placement, text layout, symbols like t₀ and R☉) | Largest single job in Phase 4; compared side by side for every figure type |
| Software drawing fails on remote desktop or virtual machines | Tested in Phase 0; Mesa fallback if needed |
| Numbers differ from the full app | Parity harness from Phase 0, run in CI on every change |
| Two codebases drift apart | Every radio change in the full app gets a matching Lite task; parity references are regenerated from the Python code in CI, so a change there fails the Lite tests until it is ported |
| No user feedback until 1.0 | You review a build at the end of every phase |
