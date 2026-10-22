# e-CALLISTO FITS Analyzer Lite

The radio part of the e-CALLISTO FITS Analyzer, written in Rust, with the same
user interface as the full app. Scope, decisions and phases are in
[PLAN.md](PLAN.md).

## Layout

| Path | Contents |
|---|---|
| `crates/callisto-core` | Everything without a GUI: FITS reading, joining, processing, rendering, archive access |
| `crates/callisto-lite` | The application (egui): theme, fonts, icons, menus, main window, plot canvas, dialogs |
| `parity/` | Checks that Lite's results match the full app's, on real files |
| `reference/windows/` | Layout, menus and plot geometry captured from the full app (the screenshots themselves are not kept in git; see below) |
| `tools/` | Capture and measurement scripts |

Icons come straight from `../assets/icons` and `../assets/icons_dark` and are
embedded when Lite is built.

## Building and running

You need a Rust toolchain ([rustup.rs](https://rustup.rs)); on Windows also the
Visual Studio Build Tools with **Desktop development with C++**. Run cargo from
PowerShell or cmd: in Git Bash, Git's `/usr/bin/link` shadows the MSVC linker.

```powershell
cd lite
cargo run --release -p callisto-lite
```

Drawing uses the GPU when there is one, Windows' or Mesa's software renderer
when there is not, and OpenGL if neither starts. `CALLISTO_LITE_GPU` forces one:
`hardware`, `software` or `opengl`. `tools/check_renderers.ps1` starts Lite each
way and reports which renderer it used.

Lite keeps its settings and its archive download cache in its own folder, apart
from the full app's. On its first start it takes over the full app's settings
(theme, presets, recent files and the like), reading them only.
`CALLISTO_LITE_OFFLINE=1` stops it asking the archive whether adjacent
observations exist and skips the update check at startup;
`CALLISTO_LITE_NO_SPLASH=1` opens the main window without the splash.

## Installers

Each builds into `lite/dist`, beside the full app (its own install folder,
application id and shortcuts), named for the release tag:
`e-CALLISTO_FITS_Analyzer_v3.2.0-lite_Setup.exe`,
`e-callisto-fits-analyzer_3.2.0-lite_amd64.deb` (package
`e-callisto-fits-analyzer-lite`) and
`e-callisto-fits-analyzer_3.2.0-lite_macOS_arm64.dmg` (Apple Silicon).

```powershell
powershell -ExecutionPolicy Bypass -File lite\packaging\windows\build_windows_installer.ps1
```

```bash
bash lite/packaging/macos/build_macos_dmg.sh
bash lite/packaging/linux/build_deb_linux.sh
```

Windows needs Inno Setup 6. On macOS, `CODESIGN_IDENTITY` (a Developer ID
Application certificate) signs the app and `NOTARY_PROFILE` (a
`notarytool` keychain profile) notarises and staples the image; without them
it is signed ad hoc. The Lite workflow builds all three on demand
(Actions → Lite (Rust) → Run workflow).

**Releasing.** One tag per release, `v3.2.0` (pre-releases `v3.3.0-beta`,
marked pre-release on GitHub), with all six installers: the full app's
(`e-CALLISTO_FITS_Analyzer_v3.2.0_Setup.exe`,
`e-callisto-fits-analyzer_3.2.0_amd64.deb`,
`e-callisto-fits-analyzer_3.2.0_macOS_arm64.dmg`) and Lite's. Lite's version
is the full app's with `-lite` (`version` in `lite/Cargo.toml`). Give the
release a title that names no platform, or all three. Upload the full app's
installers first, then Lite's: copies of the full app up to 3.1.x download
the first installer GitHub lists for their platform. Before announcing, run

```powershell
python packaging/check_release_assets.py v3.2.0
```

It checks the six names, the order, and which installer each update check
(3.1.x, today's full app, Lite) would download.

## Tests

```powershell
cargo test --workspace
```

**Results against the full app.** In the full app's Python environment, from
the repository root:

```powershell
python lite/parity/make_reference.py
cargo test -p callisto-core --test parity -- --nocapture
```

`make_reference.py` runs the full app's own functions on `parity/fixtures`; the
Rust test compares Lite's output value by value. To check that every file in a
folder opens in both:

```powershell
python lite/parity/load_all.py "C:\path\to\files"
cargo run --release -p callisto-core --example load_all -- "C:\path\to\files"
```

**Looks against the full app.** Renders Lite offscreen at 1600 × 1000 and 1.5×
scale and compares it with `reference/windows`:

```powershell
cargo test -p callisto-lite --test screenshots -- --ignored --nocapture --test-threads 1
```

Results land in `reference/compare/`: `side_by_side_*.png` (full app left,
Lite right) and `diff_*.png` (bright where they differ). `tools/probe_offsets.py`
and `tools/probe_menu.py` measure how far individual elements are apart.

The screenshot tests skip any reference image that is missing, and images are
not kept in git, so capture them first on a new checkout.

**New references.** `tools/capture_reference.py` opens the full app's empty main
window with throwaway settings and saves the window, the sidebar, every menu and
submenu, the widget layout and font metrics. `tools/capture_states.py` loads the
fixture files and saves the window in each data state (empty, one file,
combined, background subtracted, dB, UT, log axis), with pyqtgraph's plot
geometry and ticks:

```powershell
python lite/tools/capture_reference.py lite/reference/windows light
python lite/tools/capture_reference.py lite/reference/windows dark
python lite/tools/capture_states.py lite/reference/windows light
python lite/tools/capture_states.py lite/reference/windows dark
```

Run it on macOS and Linux into `reference/macos` and `reference/linux` for
those platforms' references.

**Downloads and import.** `tools/capture_downloader.py` opens the full app's
e-CALLISTO and Learmonth downloaders with throwaway settings, a scratch cache and
canned network answers built from `parity/fixtures`. It saves every tab and
state, the calendar and station popups, the previews and the import progress
dialog. Lite's side runs on the same canned answers:

```powershell
python lite/tools/capture_downloader.py lite/reference/windows/downloads light
python lite/tools/capture_downloader.py lite/reference/windows/downloads dark
cargo test -p callisto-lite --test downloader_screenshots -- --ignored --nocapture --test-threads 1
```

The downloader's results and figure layouts have their own references:

```powershell
python lite/parity/make_download_reference.py
python lite/parity/make_figure_reference.py
cargo test -p callisto-core --test downloads_parity --test figures_parity
```

**Analysis.** `tools/capture_analysis.py` loads a fixture into the full app's
main window and saves its tool states (ruler, drift rate, light curves,
annotations, isolated burst), the Maximum Intensities and Analyzer windows, the
Type II band-splitting window (picked points, fits, results, B versus R) with
its graph settings, and the analysis settings dialogs:

```powershell
python lite/tools/capture_analysis.py lite/reference/windows/analysis light
python lite/tools/capture_analysis.py lite/reference/windows/analysis dark
cargo test -p callisto-lite --test analysis_screenshots --test analysis_windows --test analysis_dialogs -- --ignored --nocapture --test-threads 1
```

**Data tools.** `tools/capture_tools.py` opens the full app's Batch FIT
Processing dialog and Multi-Station Comparison window from a main window with
a fixture loaded, with throwaway settings and a scratch folder. It runs a real
batch over three fixtures and saves the dialog states and output PNGs, the
comparison in six states (one file, four stations, two more that combine,
noise clipping, per-station dB scales, a ruler measurement) with each panel's
extent and levels, the Export Comparison dialog and both exports:

```powershell
python lite/tools/capture_tools.py lite/reference/windows/tools light
python lite/tools/capture_tools.py lite/reference/windows/tools dark
cargo test -p callisto-lite --test tools_windows --test comparison_windows -- --ignored --nocapture --test-threads 1
cargo test -p callisto-core --test comparison_parity
```

The batch and comparison grid figures are also in `make_figure_reference.py`'s
cases, checked by `figures_parity`, as are the main window's Export Figure
graphs and the analysis windows' saved figures.

**Projects, exports and reports.** `parity/make_project_reference.py` drives
the full app (throwaway settings and caches) into raw, processed, analysis and
combined states, saves each as a project, reopens it and records what it writes
back, then exports from it: Export to FIT, the analysis log, the provenance
report and Export Figure (PNG, PDF, EPS, and on a log frequency axis).
`parity/make_report_reference.py` then generates each project's report and
records the report's input and where ReportLab put everything:

```powershell
python lite/parity/make_project_reference.py
python lite/parity/make_report_reference.py
cargo test -p callisto-lite --test project_parity --test export_figure --test project_report
cargo test -p callisto-core --test report_parity
```

`CALLISTO_KEEP_FIGURES=1` keeps Lite's exported figures and reports and prints
where they are.

**Help windows.** `tools/make_user_guide.py` writes Lite's user guide from
the full app's. `tools/capture_help.py` opens the full app's help windows with
throwaway settings and Lite's name, version and guide: the User Guide (with
where Qt put each block), Cite this Software, Report a Bug, About, the update
check's boxes and download dialog, and the splash.
`parity/make_settings_reference.py` writes a settings INI file with Qt for
the first-start import's test:

```powershell
python lite/tools/make_user_guide.py
python lite/tools/capture_help.py lite/reference/windows/help light
python lite/tools/capture_help.py lite/reference/windows/help dark
python lite/parity/make_settings_reference.py
cargo test -p callisto-lite --test help_windows
cargo test -p callisto-lite --test help_windows -- --ignored --nocapture
```

**Speed.** The plan's speed targets, timed on a folder of e-CALLISTO files
(INDIA-GAURI focus code 02, a full day):

```powershell
cargo run --release -p callisto-lite --example bench -- "C:\path\to\files"
```

Start-up: set `CALLISTO_LITE_TRACE_STARTUP` to a file path and start the
release build; it writes the milliseconds to its first complete frame there.
