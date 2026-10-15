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
from the full app's. `CALLISTO_LITE_OFFLINE=1` stops it asking the archive
whether adjacent observations exist.

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

**Speed.** The plan's speed targets, timed on a folder of e-CALLISTO files
(INDIA-GAURI focus code 02, a full day):

```powershell
cargo run --release -p callisto-lite --example bench -- "C:\path\to\files"
```

Start-up: set `CALLISTO_LITE_TRACE_STARTUP` to a file path and start the
release build; it writes the milliseconds to its first complete frame there.
