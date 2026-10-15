//! e-CALLISTO FITS Analyzer Lite: the radio part of the e-CALLISTO FITS
//! Analyzer, with the same user interface, in Rust.
//!
//! See `lite/PLAN.md` for scope and phases.

pub mod app;
pub mod canvas;
pub mod fonts;
pub mod gpu;
pub mod icons;
pub mod menus;
pub mod session;
pub mod theme;
pub mod ui;

pub const APP_NAME: &str = "e-CALLISTO FITS Analyzer Lite";
pub const APP_VERSION: &str = env!("CARGO_PKG_VERSION");

/// When [`run`] started, for the start-up trace (`CALLISTO_LITE_TRACE_STARTUP`).
pub static STARTED: std::sync::OnceLock<std::time::Instant> = std::sync::OnceLock::new();

fn native_options(renderer: eframe::Renderer, gpu: gpu::GpuPreference) -> eframe::NativeOptions {
    let mut viewport = egui::ViewportBuilder::default()
        .with_title(format!("{APP_NAME} {APP_VERSION}"))
        .with_app_id("e-callisto-fits-analyzer-lite")
        .with_inner_size([1600.0, 1000.0])
        .with_min_inner_size([900.0, 600.0])
        // The full app opens maximised (window.showMaximized()).
        .with_maximized(true)
        .with_drag_and_drop(true);
    if let Some(icon) = icons::app_icon() {
        viewport = viewport.with_icon(icon);
    }
    eframe::NativeOptions { viewport, renderer, wgpu_options: gpu::wgpu_options(gpu), ..Default::default() }
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
