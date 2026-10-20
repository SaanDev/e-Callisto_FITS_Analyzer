//! The Graph Properties colormaps, with both tables the canvas uses.

use crate::radio::colormap_tables::COLORMAPS;

const PREVIEW: &str = "combined_preview";
/// Only the multi-station comparison lists it (`_COLORMAP_NAMES`).
const COMPARISON_ONLY: &str = "gray";

/// One colormap from `MainWindow.cmap_combo`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Colormap {
    pub name: &'static str,
    /// pyqtgraph's 256-entry table (`_mpl_cmap_to_lookup`), for images
    /// without NaN or gaps, and for the colour bar.
    pub lut: &'static [[u8; 4]],
    /// matplotlib's own colours (`_rgba_lut_for_cmap`), for images with them.
    pub exact: &'static [[u8; 4]],
}

impl Colormap {
    /// The colormap called `name`; the full app's default ("Custom") otherwise.
    pub fn by_name(name: &str) -> Colormap {
        let (name, lut, exact) = COLORMAPS.iter().find(|c| c.0 == name).copied().unwrap_or(COLORMAPS[0]);
        Colormap { name, lut, exact }
    }

    /// The names, in the order of the Graph Properties list.
    pub fn names() -> impl Iterator<Item = &'static str> {
        COLORMAPS.iter().map(|c| c.0).filter(|n| *n != PREVIEW && *n != COMPARISON_ONLY)
    }

    /// The Combine FITS Files preview's colormap (not in the list).
    pub fn combine_preview() -> Colormap {
        Colormap::by_name(PREVIEW)
    }
}

impl Default for Colormap {
    fn default() -> Self {
        Colormap::by_name("Custom")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lookup() {
        assert_eq!(Colormap::names().count(), 11);
        let c = Colormap::by_name("viridis");
        assert_eq!((c.lut.len(), c.exact.len()), (256, 256));
        assert_eq!(Colormap::by_name("nope").name, "Custom");
        // Custom runs blue → red → yellow.
        assert_eq!(Colormap::default().lut[0], [0, 0, 255, 255]);
        assert_eq!(Colormap::default().lut[255], [255, 255, 0, 255]);
    }
}
