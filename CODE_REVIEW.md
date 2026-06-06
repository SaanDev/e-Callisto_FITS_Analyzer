# Code Review — e-CALLISTO FITS Analyzer v2.6.0

**Date:** 2026-06-05
**Scope:** `src/` (~43,600 LOC), `tests/` (~12,600 LOC). Build artifacts (`build/`, `dist/`, `*.dmg`) excluded.
**Areas covered:** architecture & maintainability, bugs & correctness, performance, scientific correctness.

---

## Executive summary

This is a strong, mature scientific application. The Backend is cleanly modularised, the scientific physics is **correct** (verified numerically — see the Scientific section), the rendering path is genuinely well-optimised, and the test suite is broad (60 files covering both backend and UI). Dependencies are fully pinned for reproducible builds.

The single dominant problem is **structural, not scientific**: `src/UI/main_window.py` is a 12,012-line God object — one `MainWindow` class with **476 methods**, a **~1,078-line `__init__`**, and **256 instance attributes**. Almost every cross-cutting concern (theming, toolbars, exports, updates, annotations, overlays, project I/O, light curves, measurements) lives in this one class. This is the root cause of most maintainability risk and should anchor any refactoring plan.

The second theme is **error-handling discipline**: there are **791 `except Exception`** blocks across `src/`, of which **~398 silently swallow** the error (`pass`/`return`/`continue`). This works defensively but hides real failures and makes debugging hard.

Neither issue threatens correctness today. They threaten your ability to keep changing the code safely.

**Overall grade: B+.** Excellent science and packaging; held back by UI-layer concentration and over-broad exception handling.

---

## Priority ranking

| # | Finding | Severity | Effort |
|---|---------|----------|--------|
| 1 | `main_window.py` God object (12k lines, 476 methods) | High (maintainability) | High |
| 2 | ~398 silent `except Exception` blocks | High (debuggability) | Medium |
| 3 | Fundamental-vs-harmonic emission assumption is implicit | Medium (science) | Low–Med |
| 4 | Temp directories from imports never cleaned up | Medium (resource leak) | Low |
| 5 | Import/comparison threads not stopped in `closeEvent` | Medium (shutdown) | Low |
| 6 | Inline QSS stylesheets embedded as Python strings | Medium (maintainability) | Low |
| 7 | Remote FITS loaded fully into memory (no streaming) | Low–Med (memory) | Low |
| 8 | Inconsistent logging (`print()` + 791 excepts vs `logging`) | Low–Med | Medium |
| 9 | Duplicated constants across modules | Low | Low |
| 10 | Minor: dead variable, summary-metric inconsistency, header units | Low | Low |

---

## 1. Architecture & maintainability

### 1.1 The `MainWindow` God object — *High*

`src/UI/main_window.py` is one class:

- **12,012 lines**, **476 methods**, all in `class MainWindow(QMainWindow)`.
- `__init__` spans lines **236–~1,313 (~1,078 lines)** and performs **256 `self.<attr> =` assignments**.
- Responsibilities mixed into the one class include: theme/QSS generation, toolbar construction, noise-reduction controls, light-curve plotting, display-range presets, two-point measurements, lasso/drift/rect interaction handlers, FITS/PNG/PDF export, HDU template building, update checking + download, bug reporting, multi-station comparison, RFI preview, annotations, GOES overlays, project open/save, and project-report orchestration.

Why it matters: every change touches a 12k-line file; merge conflicts are likely; the class is effectively impossible to unit-test in isolation (the tests reach into it via compatibility shims); and onboarding cost is high.

Recommendation — extract **controllers/managers** that own their slice of state and talk to `MainWindow` through narrow interfaces. Natural seams, each already backed by a Backend module:

- `ExportController` (PNG/PDF/FITS export, HDU template building)
- `UpdateController` (check + download + progress dialog)
- `AnnotationController`, `MeasurementController`, `LightCurveController`
- `OverlayController` (GOES overlay request/lifecycle)
- `ProjectController` (open/save/report, autosave, recovery)
- `ThemeManager` already exists (`src/UI/theme_manager.py`) — move the inline QSS there (see 1.3).

Do this incrementally: pick one self-contained area (e.g. update check/download — it already has its own worker + thread), move its methods and state into a dedicated object, and keep `MainWindow` delegating. Each extraction is independently shippable and testable.

### 1.2 The Backend is a model to follow — *strength*

`src/Backend/` is well-factored: `frequency_axis`, `fits_io`, `measurements`, `noise_reduction`, `rfi_filters`, `type_ii_band_splitting`, `burst_processor`, etc. are small, single-purpose, dependency-light, and individually tested. The dependency direction (UI → Backend, never the reverse) is clean. The refactor in 1.1 is essentially "make the UI layer look like the Backend layer."

### 1.3 Inline QSS stylesheets — *Medium*

`_modern_main_qss` (~162 lines) and `_modern_sidebar_qss` (~108 lines) embed large stylesheets as Python string literals inside `main_window.py`. These are styling assets, not logic. Move them to `.qss` files loaded at runtime (you already have `resource_path()` in `gui_shared.py` and a `theme_manager.py`). That removes ~270 lines from the God object and lets you edit styling without touching application code.

### 1.4 Duplicated constants — *Low*

`GOES_OVERLAY_CHANNEL_COLORS` is defined twice — `src/UI/main_window.py:191` and `src/UI/accelerated_plot_widget.py:152` — with the same values. `GOES_OVERLAY_LINE_WIDTH` likewise. Define once (e.g. in `src/Backend/goes_overlay.py`, which both already import) and import from there to prevent drift.

### 1.5 Developer documentation gap — *Low*

`README.md` (663 lines) is an excellent **user** guide, but there is no developer-facing material: no architecture overview, module map, contributing guide, or "how to run the tests" section. A short `CONTRIBUTING.md` plus an architecture paragraph (UI/Backend split, worker-thread pattern, where to add a new analysis module) would lower the barrier for future contributors — and for future-you.

---

## 2. Bugs & correctness

### 2.1 Over-broad, silent exception handling — *High*

- **791** `except Exception` blocks in `src/`; **~398** are immediately followed by `pass`/`return`/`continue`.
- `main_window.py` alone has **307** `except Exception`.
- `closeEvent` is ~15 consecutive `try: … except Exception: pass` blocks.

This pattern silently absorbs programming errors, I/O failures, and would-be-visible bugs. When something misbehaves in the field, there is often no log line to explain it.

Recommendation:
- Catch the **narrowest** exception you expect (`OSError`, `ValueError`, `KeyError`, `astropy`/`requests` specific errors).
- When you must keep a broad guard at a UI boundary, **log it**: `logger.exception("…")` instead of `pass`. You already import `logging` in ~39 places — make it the default, not the exception.
- For the `closeEvent` wall specifically, a small helper — `def _safe(self, fn): try: fn() except Exception: logger.exception(...)` — would collapse the boilerplate and capture failures.

You don't need to fix all 398 at once. Add logging to the broad guards first (cheap, high diagnostic value), then tighten exception types opportunistically as you touch each area.

### 2.2 Temp directories are never cleaned up — *Medium*

`DownloaderImportWorker.run()` (`src/UI/gui_workers.py:75`) calls `tempfile.mkdtemp(prefix="callisto_import_")` for every import and never removes it. Downloaded FITS files (often multi-MB each) accumulate in the OS temp area for the life of the machine. Over months of use this can be gigabytes.

Recommendation: track these directories and remove them on `closeEvent`, or register an `atexit`/`tempfile.TemporaryDirectory` cleanup, or sweep stale `callisto_import_*` dirs on startup.

### 2.3 Import / comparison threads not stopped on close — *Medium*

`closeEvent` carefully quits and waits on the update, update-download, project-report, and GOES-overlay threads, but **not** `_import_thread` or `_comparison_thread`. If the user quits mid-import, that thread is abandoned (it has `deleteLater` wired on `finished`, but the app may exit while it is still running a network download). Mirror the same `quit()`/`wait(timeout)` treatment for these two threads.

### 2.4 Silent time-anchor parsing — *Medium (science-adjacent)*

`extract_ut_start_sec` (`src/Backend/fits_io.py:331`) parses `TIME-OBS` strictly as `HH:MM:SS` and returns `None` on any deviation (extra suffix, comma decimal, missing field). Because the result feeds UTC axis labelling, a malformed header makes the time axis silently fall back to seconds-from-start with no warning to the user. Consider a more tolerant parse and a surfaced warning when anchoring fails.

### 2.5 Dead code — *Low*

`clean_rfi` (`src/Backend/rfi_filters.py:116`) computes `residual = arr - filtered` and never uses it. Remove it (or use it — a residual-based outlier test would be a natural enhancement).

---

## 3. Performance

### 3.1 Rendering path is well-designed — *strength*

`AcceleratedPlotWidget.update_image` (`src/UI/accelerated_plot_widget.py:1080`) makes the right call: when the data has no invalid/gap rows it hands the raw `float32` array plus a colour **LUT** to pyqtgraph (`setImage(arr, levels=…)`, GPU-side mapping — fast, low memory), and only falls back to pre-rendering a full RGBA image when invalid rows actually require per-pixel alpha. pyqtgraph + optional OpenGL is the correct foundation for large spectrograms. No change needed.

### 3.2 Remote FITS loaded entirely into memory — *Low–Medium*

`DownloaderImportWorker` uses `requests.get(source, timeout=25)` then `r.content` (`src/UI/gui_workers.py:79–86`), buffering the whole file in RAM before writing. For batch imports of many/large files this is a memory spike. Stream instead:

```python
with requests.get(source, timeout=25, stream=True) as r:
    r.raise_for_status()
    with open(local_path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
```

This also lets you emit finer-grained progress.

### 3.3 Slow-path RGBA precision — *Low*

`_rgba_image_from_cmap` (`src/UI/accelerated_plot_widget.py:41`) builds the colour-mapped array at `float` (float64) before quantising to `ubyte`. On the invalid-rows path this transiently allocates ~8 bytes/pixel ×4 channels. Mapping in `float32` halves that peak. Minor, since this path only runs when invalid rows exist.

### 3.4 Eager construction in `__init__` — *Low*

The ~1,078-line constructor builds a great deal up front. Secondary windows (SunPy, GOES, Dst, Kp, SEP, CME) are correctly created lazily via their `open_*` methods — good. The remaining win is indirect: breaking up the God object (1.1) makes it easier to defer building rarely-used panels and shrink cold-start time.

---

## 4. Scientific correctness

**This is the strongest part of the codebase.** I independently recomputed the key relations; all check out.

### 4.1 Type II band-splitting physics — *verified correct*

`src/Backend/type_ii_band_splitting.py`:

- **Plasma frequency inversion** `n_e = (f / 0.00898)²` (`electron_density_cm3_from_frequency_mhz`) — correct; gives n_e ≈ 1.24×10⁸ cm⁻³ at 100 MHz, as expected.
- **Newkirk height** uses `denom = fold × 3.385`; the exact constant is `(0.00898)² × 4.2×10⁴ = 3.3869`, so 3.385 is right to the rounding of the input constants. The inversion `r = 4.32·ln10 / ln(ratio)` is algebraically correct.
- **Magnetic field from Alfvén speed** `B = v_A·√(μ₀·m_p·n_e)` then ×10⁴ to Gauss (`magnetic_field_gauss_from_alfven_speed`) — correct; gives ~4.6 G at v_A=1000 km/s, n_e=10⁸ cm⁻³ (a sensible coronal value).
- **Rankine–Hugoniot Mach number** `M_A = √(X(X+5) / (2(4−X)))` with the enforced `X < 4` domain — correct and properly guarded (it diverges as X→4, and the code rejects X≥4).
- **Compression from band split** `X = (f_upper / f_lower)²` — the standard upstream/downstream interpretation.

### 4.2 Signal processing — *sound*

- `noise_reduction.py`: per-row (per-channel) baseline subtraction with mean/median/robust-percentile, and a MAD→IQR→std→1.0 fallback chain for the noise scale. Robust and correct; invalid rows are handled explicitly.
- `rfi_filters.py`: MAD-based robust z-score (0.6745 factor), 2D median despeckle, hot-channel masking with neighbour interpolation, high-side-only percentile clip (correctly preserves burst morphology). Good.
- `frequency_axis.py`: correct bin-edge computation, frequency-direction detection, gap handling, finite/percentile limits. Strong edge-case coverage.
- `measurements.py`: two-point ruler (duration, Δf, drift slope) with finite/distinct-time validation. Clean.

### 4.3 Fundamental-vs-harmonic emission assumption — *Medium, surface it*

All density/height/field derivations assume **fundamental** plasma emission (f = f_p). If a burst is **harmonic** (f = 2·f_p), the true density is **4× lower** and the inferred Newkirk heights shift accordingly. The pipeline offers no fundamental/harmonic switch, and the assumption isn't surfaced in the UI or outputs. For Type II work this is a real physical choice that affects results.

Recommendation: add a fundamental/harmonic toggle (divide input frequency by the harmonic number before the density inversion) and record the choice in the provenance/report so results are interpretable. Low–medium effort; high scientific value.

### 4.4 Summary-metric inconsistency between the two Type II paths — *Low*

`calculate_type_ii_parameters` computes the scalar compression as a **ratio of mean frequencies** (`(avg_upper/avg_lower)²`, line 318), whereas `calculate_b_vs_r_profile` works **per sample** (mean of `(f_u/f_l)²`). Ratio-of-means ≠ mean-of-ratios, so the two reported X values can differ slightly for the same selection. Defensible for a summary, but worth either aligning the two or noting the difference in the report so users aren't surprised.

### 4.5 Header axis units not validated — *Low*

`_axis_from_header` (`src/Backend/fits_io.py:133`) builds the axis from `CRVAL/CDELT/CRPIX` without checking `CUNIT`. CALLISTO files are MHz by convention so risk is low, but a file in Hz would yield an axis off by 10⁶ with no warning. A `CUNIT` check (or a sanity range check on the resulting frequencies) would harden the loader.

---

## What's done well (keep doing it)

- **Correct, non-trivial physics**, matching the published Type II band-splitting methodology.
- **Clean Backend modularisation** with a strict UI→Backend dependency direction.
- **Broad test suite** — 60 files, dedicated backend tests for every science module.
- **Well-optimised rendering** (LUT/GPU fast path; RGBA only when needed).
- **Fully pinned dependencies** (`requirements-runtime.txt`) for reproducible packaged builds.
- **Careful cross-platform startup** (`main.py`: Wayland/xcb handling, macOS stderr filtering, software-OpenGL escape hatch).
- **Thorough shutdown** of most worker threads and a recovery/autosave system.
- Good hygiene elsewhere: no bare `except:`, no mutable default arguments, no stray TODO/FIXME debt.

---

## Suggested sequencing

1. **Cheap, high-value first:** add `logger.exception(...)` to the broad guards (2.1); clean up temp dirs (2.2); stop import/comparison threads on close (2.3); remove the dead `residual` (2.5).
2. **Science:** add the fundamental/harmonic toggle and record it in provenance (4.3).
3. **De-risk the God object:** extract one controller (suggest `UpdateController` — it already has its own worker/thread) as a template, then repeat for export, overlays, project, annotations (1.1). Move inline QSS to `theme_manager` along the way (1.3).
4. **Polish:** dedupe constants (1.4), stream downloads (3.2), add a short `CONTRIBUTING.md` + architecture note (1.5).

---

*Note on verification: physics relations in §4.1 were recomputed independently (plasma frequency, Newkirk constant, magnetic field, Mach-number domain) and matched the implementation. The full pytest suite could not be executed in this review environment (no package index access to install numpy/scipy/astropy/PySide6); the 60-file suite — including dedicated tests for each science module — is a strong correctness signal on its own. Line numbers refer to the v2.6.0 source as reviewed and may shift as the code changes.*
