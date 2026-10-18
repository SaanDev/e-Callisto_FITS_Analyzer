//! Choosing how the window is drawn.
//!
//! Lite has to start on old station PCs, remote-desktop sessions and virtual
//! machines, so it tries, in order:
//! 1. a real GPU through wgpu (DirectX 12, Metal or Vulkan, then OpenGL ES),
//! 2. a software wgpu adapter (WARP on Windows, llvmpipe/lavapipe on Linux),
//! 3. eframe's OpenGL renderer (glow), which can use a Mesa `opengl32.dll`
//!    placed next to the executable, the way Qt ships `opengl32sw.dll`.
//!
//! `CALLISTO_LITE_GPU=hardware|software|opengl` forces one of these, for
//! testing and as a workaround on machines where the automatic choice fails.

use std::sync::{Arc, Mutex};

use eframe::egui_wgpu::{self, WgpuSetup, WgpuSetupCreateNew, wgpu};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum GpuPreference {
    #[default]
    Auto,
    Hardware,
    Software,
    OpenGl,
}

impl GpuPreference {
    pub fn from_env() -> Self {
        match std::env::var("CALLISTO_LITE_GPU").map(|v| v.to_ascii_lowercase()).as_deref() {
            Ok("hardware") => GpuPreference::Hardware,
            Ok("software") => GpuPreference::Software,
            Ok("opengl") | Ok("glow") => GpuPreference::OpenGl,
            _ => GpuPreference::Auto,
        }
    }
}

/// What was actually used, for the bug-report dialog and diagnostics.
static CHOSEN: Mutex<Option<String>> = Mutex::new(None);

pub fn chosen_renderer() -> Option<String> {
    CHOSEN.lock().ok().and_then(|c| c.clone())
}

pub fn record(description: impl Into<String>) {
    let description = description.into();
    eprintln!("callisto-lite: drawing with {description}");
    if let Ok(mut c) = CHOSEN.lock() {
        *c = Some(description);
    }
}

pub fn wgpu_options(preference: GpuPreference) -> egui_wgpu::WgpuConfiguration {
    let mut setup = WgpuSetupCreateNew::without_display_handle();
    setup.native_adapter_selector =
        Some(Arc::new(move |adapters: &[wgpu::Adapter], surface: Option<&wgpu::Surface<'_>>| {
            select_adapter(adapters, surface, preference)
        }));
    egui_wgpu::WgpuConfiguration { wgpu_setup: WgpuSetup::CreateNew(setup), ..Default::default() }
}

/// Lower is better: hardware before software, then the platform's own API
/// (DirectX 12 on Windows, where its drivers are the most dependable; Metal
/// on macOS; Vulkan on Linux), then OpenGL ES.
fn rank(info: &wgpu::AdapterInfo) -> (u8, u8) {
    let device = match info.device_type {
        wgpu::DeviceType::DiscreteGpu => 0,
        wgpu::DeviceType::IntegratedGpu => 1,
        wgpu::DeviceType::VirtualGpu => 2,
        wgpu::DeviceType::Other => 3,
        wgpu::DeviceType::Cpu => 4,
    };
    let backend = match info.backend {
        wgpu::Backend::Dx12 if cfg!(target_os = "windows") => 0,
        wgpu::Backend::Metal | wgpu::Backend::Vulkan if !cfg!(target_os = "windows") => 0,
        wgpu::Backend::Dx12 | wgpu::Backend::Metal | wgpu::Backend::Vulkan => 1,
        wgpu::Backend::Gl => 2,
        _ => 3,
    };
    (device, backend)
}

pub fn select_adapter(
    adapters: &[wgpu::Adapter],
    surface: Option<&wgpu::Surface<'_>>,
    preference: GpuPreference,
) -> Result<wgpu::Adapter, String> {
    let usable: Vec<&wgpu::Adapter> =
        adapters.iter().filter(|a| surface.is_none_or(|s| a.is_surface_supported(s))).collect();
    for a in &usable {
        let info = a.get_info();
        eprintln!("callisto-lite: adapter {} ({:?}, {:?})", info.name, info.device_type, info.backend);
    }

    let is_software = |a: &&&wgpu::Adapter| a.get_info().device_type == wgpu::DeviceType::Cpu;
    let pick = match preference {
        GpuPreference::Software => usable.iter().filter(is_software).min_by_key(|a| rank(&a.get_info())),
        GpuPreference::Hardware => usable.iter().filter(|a| !is_software(a)).min_by_key(|a| rank(&a.get_info())),
        GpuPreference::Auto | GpuPreference::OpenGl => usable.iter().min_by_key(|a| rank(&a.get_info())),
    };

    match pick {
        Some(adapter) => {
            let info = adapter.get_info();
            let kind = if info.device_type == wgpu::DeviceType::Cpu { "software" } else { "GPU" };
            record(format!("{} ({kind}, {:?})", info.name, info.backend));
            Ok((*adapter).clone())
        }
        None => Err(format!("no {preference:?} graphics adapter among {} found", usable.len())),
    }
}
