//! Colours of the full app's Modern view, so Lite looks the same.
//!
//! Every value is copied from the full app's stylesheets:
//! * app-wide: `src/ui/common/theme_manager.py`, `AppTheme._modern_qss`
//! * sidebar surface: `src/ui/app/main_window.py`, `_sidebar_panel_qss`
//! * sidebar cards: `src/ui/app/main_window.py`, `_modern_sidebar_qss`
//! * Qt palette colours that show through (plot background, toolbar
//!   separators): Qt's Fusion palettes and `AppTheme._dark_palette`.

use egui::Color32;
use serde::{Deserialize, Serialize};

/// View → Theme, as in the full app.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum ThemeMode {
    #[default]
    System,
    Light,
    Dark,
}

impl ThemeMode {
    pub fn label(self) -> &'static str {
        match self {
            ThemeMode::System => "System",
            ThemeMode::Light => "Light",
            ThemeMode::Dark => "Dark",
        }
    }

    /// Whether this mode draws dark, given the operating system's scheme.
    pub fn is_dark(self, os_dark: bool) -> bool {
        match self {
            ThemeMode::System => os_dark,
            ThemeMode::Light => false,
            ThemeMode::Dark => true,
        }
    }
}

const fn hex(rgb: u32) -> Color32 {
    Color32::from_rgb((rgb >> 16) as u8, (rgb >> 8) as u8, rgb as u8)
}

#[derive(Clone, Debug, PartialEq)]
pub struct Palette {
    pub dark: bool,

    // App-wide (AppTheme._modern_qss).
    pub page_bg: Color32,
    pub surface_bg: Color32,
    pub surface_alt: Color32,
    pub input_bg: Color32,
    pub border: Color32,
    pub text: Color32,
    pub muted: Color32,
    pub hover: Color32,
    pub pressed: Color32,
    pub accent: Color32,
    pub accent_soft: Color32,
    pub disabled: Color32,

    // Sidebar surface and scrollbar (_sidebar_panel_qss).
    pub sidebar_panel: Color32,
    pub sidebar_handle: Color32,
    pub sidebar_handle_hover: Color32,

    // Sidebar cards (_modern_sidebar_qss).
    pub card: Color32,
    pub card_collapsed: Color32,
    pub hairline: Color32,
    pub field: Color32,
    pub field_alt: Color32,
    pub card_text: Color32,
    pub heading: Color32,
    pub card_muted: Color32,
    pub card_accent: Color32,
    pub card_accent_soft: Color32,
    pub card_accent_text: Color32,
    pub control: Color32,
    pub control_hover: Color32,

    /// Qt palette `Window`: the empty plot area and the toolbar separators.
    pub qt_window: Color32,
    /// The rest of the Qt palette (matplotlib figures, Fusion tab panes).
    pub qt: QtPalette,

    /// Opacity Qt gives a disabled toolbar icon (QIcon::Disabled).
    pub disabled_icon_alpha: f32,
}

/// The Qt palette roles Fusion and `AppTheme.apply_mpl` read.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct QtPalette {
    pub window: Color32,
    pub window_text: Color32,
    pub base: Color32,
    pub button: Color32,
    pub mid: Color32,
    pub text: Color32,
    pub highlight: Color32,
    /// `QPalette::Dark` and `Light`: the shades of `qDrawShadePanel` frames
    /// (measured from the full app's colour dialog).
    pub dark: Color32,
    pub light: Color32,
}

impl QtPalette {
    pub fn new(dark: bool, os_dark: bool) -> Self {
        if dark {
            // AppTheme._dark_palette.
            QtPalette { window: hex(0x2b2b2b), window_text: hex(0xf0f0f0), base: hex(0x1f1f1f), button: hex(0x353535), mid: hex(0x5a5a5a), text: hex(0xf0f0f0), highlight: hex(0x0a84ff), dark: hex(0x1e1e1e), light: hex(0x787878) }
        } else if os_dark {
            // Fusion's standard palette, dark scheme.
            QtPalette { window: hex(0x323232), window_text: hex(0xf0f0f0), base: hex(0x242424), button: hex(0x323232), mid: hex(0x262626), text: hex(0xf0f0f0), highlight: hex(0x308cc6), dark: hex(0x212121), light: hex(0x4b4b4b) }
        } else {
            QtPalette { window: hex(0xefefef), window_text: hex(0x000000), base: hex(0xffffff), button: hex(0xefefef), mid: hex(0xb8b8b8), text: hex(0x000000), highlight: hex(0x308cc6), dark: hex(0x9f9f9f), light: hex(0xffffff) }
        }
    }

    /// Fusion's `buttonColor`: the button colour lifted (`lighter`) and
    /// desaturated (greys stay grey).
    pub fn fusion_button(&self) -> Color32 {
        let c = self.button;
        let gray = (c.r() as i32 * 11 + c.g() as i32 * 16 + c.b() as i32 * 5) / 32;
        lighter(c, 100 + ((180 - gray) / 6).max(1))
    }

    /// Fusion's tab-widget pane (`tabFrameColor`).
    pub fn tab_frame(&self) -> Color32 {
        lighter(self.fusion_button(), 104)
    }
}

impl Palette {
    /// `os_dark` matters in light mode too: the full app then uses Fusion's
    /// standard palette, which follows the operating system's scheme.
    pub fn new(dark: bool, os_dark: bool) -> Self {
        let qt_window = if dark {
            hex(0x2b2b2b) // AppTheme._dark_palette
        } else if os_dark {
            hex(0x323232) // Fusion standard palette, dark scheme
        } else {
            hex(0xefefef) // Fusion standard palette, light scheme
        };

        if dark {
            Self {
                dark,
                page_bg: hex(0x0f151e),
                surface_bg: hex(0x171f2b),
                surface_alt: hex(0x202b3b),
                input_bg: hex(0x121a25),
                border: hex(0x314055),
                text: hex(0xe8eef8),
                muted: hex(0x9db0c9),
                hover: hex(0x29364a),
                pressed: hex(0x334760),
                accent: hex(0x4ea3ff),
                accent_soft: hex(0x1f3650),
                disabled: hex(0x6f8098),

                sidebar_panel: hex(0x151b24),
                sidebar_handle: hex(0x39465a),
                sidebar_handle_hover: hex(0x4a5a72),

                card: hex(0x1e2733),
                card_collapsed: hex(0x1a222c),
                hairline: hex(0x2c3849),
                field: hex(0x141b24),
                field_alt: hex(0x1a222c),
                card_text: hex(0xe6ecf5),
                heading: hex(0xf2f6fb),
                card_muted: hex(0x93a2b8),
                card_accent: hex(0x4d9fff),
                card_accent_soft: hex(0x22344b),
                card_accent_text: hex(0xbcd9ff),
                control: hex(0x26313f),
                control_hover: hex(0x2f3c4d),

                qt_window,
                qt: QtPalette::new(dark, os_dark),
                disabled_icon_alpha: 0.86,
            }
        } else {
            Self {
                dark,
                page_bg: hex(0xf4f7fc),
                surface_bg: hex(0xffffff),
                surface_alt: hex(0xf5f9ff),
                input_bg: hex(0xffffff),
                border: hex(0xd3dcea),
                text: hex(0x202a36),
                muted: hex(0x61758f),
                hover: hex(0xecf3ff),
                pressed: hex(0xdfeaff),
                accent: hex(0x146fda),
                accent_soft: hex(0xe8f2ff),
                disabled: hex(0x98a9bf),

                sidebar_panel: hex(0xeef2f7),
                sidebar_handle: hex(0xc6d0dd),
                sidebar_handle_hover: hex(0xadbaca),

                card: hex(0xffffff),
                card_collapsed: hex(0xfbfcfe),
                hairline: hex(0xe3e9f1),
                field: hex(0xf7f9fc),
                field_alt: hex(0xeef2f8),
                card_text: hex(0x1b2532),
                heading: hex(0x0f1722),
                card_muted: hex(0x64748b),
                card_accent: hex(0x0b74d4),
                card_accent_soft: hex(0xe8f1fd),
                card_accent_text: hex(0x0b5aa6),
                control: hex(0xf2f5f9),
                control_hover: hex(0xe6ecf4),

                qt_window,
                qt: QtPalette::new(dark, os_dark),
                disabled_icon_alpha: 0.79,
            }
        }
    }

    /// Qt's `QColor::darker(110)` / `lighter(110)` of the palette window,
    /// which Fusion uses for toolbar separators.
    pub fn separator_dark(&self) -> Color32 {
        scale(self.qt_window, 1.0 / 1.1)
    }

    pub fn separator_light(&self) -> Color32 {
        scale(self.qt_window, 1.1)
    }

    /// Apply the colours egui's own widgets use (menus, popups, tooltips,
    /// scroll bars), matching the full app's QMenu and QToolTip rules.
    pub fn apply(&self, ctx: &egui::Context) {
        let mut visuals = if self.dark { egui::Visuals::dark() } else { egui::Visuals::light() };
        visuals.panel_fill = self.page_bg;
        visuals.window_fill = self.surface_bg;
        visuals.window_stroke = egui::Stroke::new(1.0, self.border);
        visuals.window_corner_radius = egui::CornerRadius::same(10);
        visuals.menu_corner_radius = egui::CornerRadius::same(10);
        visuals.window_shadow = egui::Shadow::NONE;
        visuals.popup_shadow = egui::Shadow::NONE;
        visuals.extreme_bg_color = self.input_bg;
        visuals.faint_bg_color = self.surface_alt;
        visuals.override_text_color = None;
        visuals.selection.bg_fill = self.accent;
        visuals.selection.stroke = egui::Stroke::new(1.0, Color32::WHITE);
        visuals.hyperlink_color = self.accent;

        let w = &mut visuals.widgets;
        for state in [&mut w.noninteractive, &mut w.inactive, &mut w.hovered, &mut w.active, &mut w.open] {
            state.corner_radius = egui::CornerRadius::same(7);
            state.fg_stroke = egui::Stroke::new(1.0, self.text);
            state.bg_stroke = egui::Stroke::NONE;
            state.expansion = 0.0;
        }
        w.noninteractive.bg_fill = self.surface_bg;
        w.noninteractive.weak_bg_fill = self.surface_bg;
        w.noninteractive.bg_stroke = egui::Stroke::new(1.0, self.border); // separators
        w.noninteractive.fg_stroke = egui::Stroke::new(1.0, self.text);
        w.inactive.bg_fill = Color32::TRANSPARENT;
        w.inactive.weak_bg_fill = Color32::TRANSPARENT;
        w.hovered.bg_fill = self.hover;
        w.hovered.weak_bg_fill = self.hover;
        w.active.bg_fill = self.pressed;
        w.active.weak_bg_fill = self.pressed;
        w.open.bg_fill = self.hover;
        w.open.weak_bg_fill = self.hover;
        visuals.disabled_alpha = 0.55;

        ctx.set_visuals(visuals);
    }
}

/// `QColor::lighter(factor)` on its 16-bit HSV value, for greyish colours.
pub fn lighter(c: Color32, factor: i32) -> Color32 {
    let ch = |v: u8| ((v as i32 * 257 * factor / 100).min(65535) / 257) as u8;
    Color32::from_rgb(ch(c.r()), ch(c.g()), ch(c.b()))
}

/// `QColor::darker(factor)` on its 16-bit HSV value, for greyish colours.
pub fn darker(c: Color32, factor: i32) -> Color32 {
    let ch = |v: u8| ((v as i32 * 257 * 100 / factor) / 257) as u8;
    Color32::from_rgb(ch(c.r()), ch(c.g()), ch(c.b()))
}

/// `QColor::darker(factor × 100)`, roughly: each channel divided by `f`.
pub fn darken(c: Color32, f: f32) -> Color32 {
    scale(c, 1.0 / f)
}

fn scale(c: Color32, f: f32) -> Color32 {
    let ch = |v: u8| ((v as f32 * f).round().clamp(0.0, 255.0)) as u8;
    Color32::from_rgb(ch(c.r()), ch(c.g()), ch(c.b()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fusion_pane_matches_samples() {
        // The preview window's tab pane in the light theme on a dark OS.
        assert_eq!(QtPalette::new(false, true).tab_frame(), hex(0x3e3e3e));
    }

    #[test]
    fn separators_match_fusion_samples() {
        // Measured on the full app's toolbar (lite/reference/windows).
        let light_on_dark_os = Palette::new(false, true);
        assert_eq!(light_on_dark_os.separator_dark(), hex(0x2d2d2d));
        assert_eq!(light_on_dark_os.separator_light(), hex(0x373737));
        let dark = Palette::new(true, true);
        assert_eq!(dark.separator_dark(), hex(0x272727));
        assert_eq!(dark.separator_light(), hex(0x2f2f2f));
    }
}
