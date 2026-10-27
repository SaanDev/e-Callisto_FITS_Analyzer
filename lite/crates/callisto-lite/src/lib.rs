//! e-CALLISTO FITS Analyzer Lite: the radio part of the e-CALLISTO FITS
//! Analyzer, with the same user interface, in Rust.
//!
//! See `lite/PLAN.md` for scope and phases.

pub mod analysis;
pub mod app;
pub mod canvas;
pub mod downloads;
pub mod figure_export;
pub mod figure_view;
pub mod fonts;
pub mod gpu;
pub mod help;
pub mod icons;
pub mod menus;
pub mod project;
pub mod report_dialog;
pub mod session;
pub mod settings_import;
pub mod theme;
pub mod tools;
pub mod ui;
pub mod vector_export;

pub const APP_NAME: &str = "e-CALLISTO FITS Analyzer Lite";
pub const APP_VERSION: &str = env!("CARGO_PKG_VERSION");
/// The official website (Help → Official Website, About).
pub const APP_WEBSITE: &str = env!("CARGO_PKG_HOMEPAGE");

/// When [`run`] started, for the start-up trace (`CALLISTO_LITE_TRACE_STARTUP`).
pub static STARTED: std::sync::OnceLock<std::time::Instant> = std::sync::OnceLock::new();

/// Whether the window opens as the startup splash (`CALLISTO_LITE_NO_SPLASH`
/// opens the main window straight away).
pub fn splash_enabled() -> bool {
    std::env::var_os("CALLISTO_LITE_NO_SPLASH").is_none_or(|v| v.is_empty() || v == "0")
}

fn native_options(renderer: eframe::Renderer, gpu: gpu::GpuPreference) -> eframe::NativeOptions {
    let mut viewport = egui::ViewportBuilder::default()
        .with_title(format!("{APP_NAME} {APP_VERSION}"))
        .with_app_id("e-callisto-fits-analyzer-lite")
        .with_drag_and_drop(true);
    viewport = if splash_enabled() {
        // The splash first (frameless, centred); the app then maximises it.
        viewport.with_inner_size(ui::splash::SIZE).with_decorations(false).with_resizable(false)
    } else {
        // The full app opens maximised (window.showMaximized()).
        viewport.with_inner_size([1600.0, 1000.0]).with_min_inner_size([900.0, 600.0]).with_maximized(true)
    };
    if let Some(icon) = icons::app_icon() {
        viewport = viewport.with_icon(icon);
    }
    eframe::NativeOptions {
        viewport,
        renderer,
        centered: true,
        persist_window: false,
        wgpu_options: gpu::wgpu_options(gpu),
        ..Default::default()
    }
}

/// Start the application: wgpu first (GPU, then a software adapter), and
/// OpenGL if wgpu cannot start at all. See `gpu`.
pub fn run() -> eframe::Result {
    let _ = STARTED.set(std::time::Instant::now());
    let gpu = gpu::GpuPreference::from_env();
    let start = |renderer| {
        eframe::run_native(APP_NAME, native_options(renderer, gpu), Box::new(|cc| Ok(Box::new(app::LiteApp::new(cc)))))
    };
    if gpu == gpu::GpuPreference::OpenGl {
        gpu::record("OpenGL (glow), forced");
        return start(eframe::Renderer::Glow);
    }
    match start(eframe::Renderer::Wgpu) {
        Err(eframe::Error::Wgpu(err)) => {
            eprintln!("callisto-lite: wgpu could not start ({err}); trying OpenGL");
            gpu::record("OpenGL (glow), wgpu failed");
            start(eframe::Renderer::Glow)
        }
        other => other,
    }
}
