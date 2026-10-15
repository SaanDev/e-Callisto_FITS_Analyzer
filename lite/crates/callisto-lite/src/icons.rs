//! The full app's own SVG icons, embedded from `assets/` at build time.
//!
//! Light and dark sets come from `assets/icons` and `assets/icons_dark`, the
//! folders the full app switches between with the theme.

use egui::ImageSource;
use egui::load::Bytes;

macro_rules! icon_set {
    ($($name:ident => $file:literal),* $(,)?) => {
        #[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
        pub enum Icon {
            $($name),*
        }

        impl Icon {
            pub const ALL: &'static [Icon] = &[$(Icon::$name),*];

            pub fn file_name(self) -> &'static str {
                match self {
                    $(Icon::$name => $file),*
                }
            }

            pub fn source(self, dark: bool) -> ImageSource<'static> {
                match (self, dark) {
                    $(
                        (Icon::$name, false) => ImageSource::Bytes {
                            uri: concat!("bytes://icons/", $file).into(),
                            bytes: Bytes::Static(include_bytes!(concat!("../../../../assets/icons/", $file))),
                        },
                        (Icon::$name, true) => ImageSource::Bytes {
                            uri: concat!("bytes://icons_dark/", $file).into(),
                            bytes: Bytes::Static(include_bytes!(concat!("../../../../assets/icons_dark/", $file))),
                        },
                    )*
                }
            }
        }
    };
}

icon_set! {
    ChevronDownSmall => "chevron_down_small.svg",
    ChevronUpSmall => "chevron_up_small.svg",
    Download => "download.svg",
    Drift => "drift.svg",
    Export => "export.svg",
    ExportFits => "export_fits.svg",
    ExportProject => "export_proj.svg",
    Isolate => "isolate.svg",
    Lock => "lock.svg",
    Max => "max.svg",
    Open => "open.svg",
    Redo => "redo.svg",
    ResetAll => "reset_all.svg",
    ResetSelection => "reset_selection.svg",
    ResetToRaw => "reset_to_raw.svg",
    Undo => "undo.svg",
    Unlock => "unlock.svg",
    Zoom => "zoom.svg",
}

/// The application icon (`assets/branding/FITS_analyzer.png`).
pub fn app_icon() -> Option<egui::IconData> {
    let png = include_bytes!("../../../../assets/branding/FITS_analyzer.png");
    let img = image::load_from_memory_with_format(png, image::ImageFormat::Png).ok()?.into_rgba8();
    let (width, height) = img.dimensions();
    Some(egui::IconData { rgba: img.into_raw(), width, height })
}
