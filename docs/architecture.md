# Project structure

The application keeps scientific processing in `src/backend`, Qt presentation in
`src/ui`, and process/resource paths in `src/core`. Both application layers are
organized by feature so related files stay together.

```text
src/
  __main__.py              # python -m src
  main.py                  # Startup, platform setup, helper-process modes
  version.py               # Shared application metadata
  core/                    # Source and packaged-app paths
  backend/
    common/                # Array statistics and figure export
    radio/                 # FITS, instrument readers, archives, spectrum processing
    solar/                 # Solar archives, images, calibration, reprojection
    gcs/                   # CME/shock models, figures and fitting reports
    space_weather/         # GOES, Dst, Kp and proton data
    session/               # Projects, sessions, annotations, history and reports
    services/              # Downloads, settings, updates and bug reports
  ui/
    app/                   # Main window, workers and startup loading
    common/                # Shared UI helpers, fonts, themes and URL opening
    widgets/               # Reusable plotting and measurement widgets
    downloads/             # Radio archive downloaders and download queue
    radio/                 # Burst views, FITS headers and radio analysis dialogs
    solar/                 # Solar image viewers, analysis and measurement tools
    gcs/                   # Fitting window, viewpoint panels and export actions
    cme/                   # CME catalogue/movie viewer and helper IPC
    space_weather/         # GOES, Dst and Kp windows
    help/                  # User guide, citation and bug-report dialogs
    gui_main.py            # Existing aggregate GUI facade
assets/
  branding/                # One canonical app logo plus ICO and ICNS files
  icons/                   # Light UI icons
  icons_dark/              # Dark UI icons
  band_splitting_icons/    # Feature-specific light/dark icons
  screenshots/             # Documentation screenshots
packaging/
  pyinstaller/             # Freezer specs, hooks and runtime hooks
  windows/                 # Windows build, repair and Inno Setup scripts
  macos/                   # py2app, signing, repair and DMG scripts
  linux/                   # Debian build, desktop integration and launcher
requirements/              # Runtime, build and development dependencies
scripts/                   # Dependency installation and packaged-app smoke test
tests/
  backend/                 # Scientific processing and services
  ui/                      # Qt presentation and integration
  packaging/               # Build configuration and resource-path checks
  helpers/                 # Reusable FITS/instrument test data builders
docs/
  build/                   # Platform build guides
  release_notes/           # Versioned release notes
```

## Running and testing

Run commands from the repository root with the project's virtual environment active:

```sh
python scripts/install_requirements.py
python -m src

# The direct script also supports helper subprocesses and freezer entry points.
python src/main.py

python -m pip install -r requirements/requirements-dev.txt
python -m pytest
python -m pytest tests/backend
python -m pytest tests/ui --timeout=60
python -m pytest tests/packaging
```

For headless runs, set `QT_QPA_PLATFORM=offscreen`. In a sandbox, point
`SUNPY_CONFIGDIR` and `MPLCONFIGDIR` at writable temporary directories. The
download-manager tests start a loopback HTTP server and require local socket
permissions.

## Connections and placement rules

- Import application modules by their complete package path, for example
  `from src.backend.radio.fits_io import load_callisto_fits`.
  Package initializers stay lightweight; feature modules load optional scientific
  dependencies when needed.
- Backend modules do not import Qt UI modules. Shared computation belongs in
  `backend/common`; shared Qt behavior belongs in `ui/common` or `ui/widgets`.
- Source subprocesses use `src.core.paths.source_main_path()` rather than counting
  parent directories. Frozen subprocesses relaunch the packaged executable with
  a helper mode.
- Resource lookup uses `src.core.paths` and `ui.common.gui_shared.resource_path()`.
  All freezer configurations preserve the `assets/...` layout inside the bundle.
- New dynamically imported modules may require updates to the PyInstaller
  hidden-import lists and the py2app includes in `packaging/macos/setup.py`.
- Test helpers belong in `tests/helpers`, and test-to-test imports use the complete
  `tests.ui...` or `tests.backend...` package path.
- Local observations and saved projects remain in the ignored `projects/` folder.
  Virtual environments, IDE settings, caches, and generated build outputs remain
  local and are not application source.

## Migration from the previous layout

`src/Backend` and `src/UI` have been replaced by the feature packages above.
`src/Installation` has been split among `packaging`, `requirements`, and `scripts`.
Update local IDE run configurations to use module `src` or script `src/main.py`.
Internal imports use the new paths; the previous flat module paths are no longer
entry points. The main window and helper modes retain their existing behavior.
