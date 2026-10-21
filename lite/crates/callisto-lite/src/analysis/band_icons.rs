//! The Type II window's tool-button icons (`assets/band_splitting_icons`,
//! a light and a dark set), drawn as Qt draws a `QIcon` made from an SVG:
//! rendered at the button's icon size in device pixels, and for a
//! disabled button turned into `QCommonStyle::generatedIconPixmap`'s
//! disabled image over the palette's window colour.

use egui::{Color32, ColorImage};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum BandIcon {
    AddPoints,
    Undo,
    Clear,
    FitActive,
    FitBoth,
    SavePlot,
    Bvr,
    Settings,
}

macro_rules! svg {
    ($dark:expr, $file:literal) => {
        if $dark {
            include_bytes!(concat!("../../../../../assets/band_splitting_icons/dark/", $file)).as_slice()
        } else {
            include_bytes!(concat!("../../../../../assets/band_splitting_icons/light/", $file)).as_slice()
        }
    };
}

impl BandIcon {
    /// The toolbar's order (`controls.addWidget`).
    pub const ALL: [BandIcon; 8] = [
        BandIcon::AddPoints,
        BandIcon::Undo,
        BandIcon::Clear,
        BandIcon::FitActive,
        BandIcon::FitBoth,
        BandIcon::SavePlot,
        BandIcon::Bvr,
        BandIcon::Settings,
    ];

    /// The button's tool tip and accessible name.
    pub fn label(self) -> &'static str {
        match self {
            BandIcon::AddPoints => "Add Points",
            BandIcon::Undo => "Undo Last Point",
            BandIcon::Clear => "Clear Active Band",
            BandIcon::FitActive => "Fit Active Band",
            BandIcon::FitBoth => "Fit Both Bands",
            BandIcon::SavePlot => "Save Plot",
            BandIcon::Bvr => "BvR",
            BandIcon::Settings => "Settings",
        }
    }

    fn svg(self, dark: bool) -> &'static [u8] {
        match self {
            BandIcon::AddPoints => svg!(dark, "add_points.svg"),
            BandIcon::Undo => svg!(dark, "undo_last_point.svg"),
            BandIcon::Clear => svg!(dark, "clear_active_band.svg"),
            BandIcon::FitActive => svg!(dark, "fit_active_band.svg"),
            BandIcon::FitBoth => svg!(dark, "fit_both_bands.svg"),
            BandIcon::SavePlot => svg!(dark, "save_plot.svg"),
            BandIcon::Bvr => svg!(dark, "plot_BvR.svg"),
            BandIcon::Settings => svg!(dark, "settings.svg"),
        }
    }
}

/// The icon at `side` × `side` device pixels; `disabled_window` is the
/// palette's disabled window colour for a disabled button.
pub fn render(icon: BandIcon, dark: bool, side: u32, disabled_window: Option<Color32>) -> Option<ColorImage> {
    let tree = resvg::usvg::Tree::from_data(icon.svg(dark), &resvg::usvg::Options::default()).ok()?;
    let mut pixmap = resvg::tiny_skia::Pixmap::new(side, side)?;
    let size = tree.size();
    let t = resvg::tiny_skia::Transform::from_scale(side as f32 / size.width(), side as f32 / size.height());
    resvg::render(&tree, t, &mut pixmap.as_mut());
    let mut rgba: Vec<[u8; 4]> = pixmap
        .pixels()
        .iter()
        .map(|p| {
            let c = p.demultiply();
            [c.red(), c.green(), c.blue(), c.alpha()]
        })
        .collect();
    if let Some(bg) = disabled_window {
        disable(&mut rgba, bg);
    }
    let bytes: Vec<u8> = rgba.into_iter().flatten().collect();
    Some(ColorImage::from_rgba_unmultiplied([side as usize, side as usize], &bytes))
}

/// `QCommonStyle::generatedIconPixmap(QIcon::Disabled)`: each pixel's grey
/// through a colour table running black → window colour → white, shifted
/// for contrast by the window colour's intensity; alpha kept.
fn disable(pixels: &mut [[u8; 4]], bg: Color32) {
    let (r, g, b) = (bg.r() as i32, bg.g() as i32, bg.b() as i32);
    let mut table = [[0u8; 3]; 256];
    for i in 0..128 {
        table[i] = [
            ((r * ((i as i32) << 1)) >> 8) as u8,
            ((g * ((i as i32) << 1)) >> 8) as u8,
            ((b * ((i as i32) << 1)) >> 8) as u8,
        ];
        table[i + 128] = [
            (r + ((i as i32) << 1)).min(255) as u8,
            (g + ((i as i32) << 1)).min(255) as u8,
            (b + ((i as i32) << 1)).min(255) as u8,
        ];
    }
    // `qt_intensity`: 30 % red, 59 % green, 11 % blue.
    let mut intensity = (77 * r + 150 * g + 28 * b) / 255;
    const FACTOR: i32 = 191;
    if (r - FACTOR > g && r - FACTOR > b) || (g - FACTOR > r && g - FACTOR > b) || (b - FACTOR > r && b - FACTOR > g) {
        intensity = (intensity + 91).min(255);
    } else if intensity <= 128 {
        intensity -= 51;
    }
    for p in pixels {
        let gray = (p[0] as i32 * 11 + p[1] as i32 * 16 + p[2] as i32 * 5) / 32;
        let ci = (gray / 3 + (130 - intensity / 3)).clamp(0, 255) as usize;
        let c = table[ci];
        *p = [c[0], c[1], c[2], p[3]];
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn disabled_colours_match_the_capture() {
        // Black on the light theme's (50, 50, 50) palette, white on the dark theme's (43, 43, 43).
        let mut px = [[0, 0, 0, 255]];
        disable(&mut px, Color32::from_rgb(50, 50, 50));
        assert_eq!(px[0], [54, 54, 54, 255]);
        let mut px = [[255, 255, 255, 255]];
        disable(&mut px, Color32::from_rgb(43, 43, 43));
        assert_eq!(px[0], [221, 221, 221, 255]);
    }

    #[test]
    fn every_icon_renders() {
        for icon in BandIcon::ALL {
            for dark in [false, true] {
                assert!(render(icon, dark, 60, None).is_some(), "{icon:?} {dark}");
            }
        }
    }
}
