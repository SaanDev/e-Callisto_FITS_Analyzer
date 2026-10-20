//! Analyze → Ridge Tracking Settings... in the Maximum Intensities window
//! (`MaxIntensityPlotDialog.edit_ridge_settings`): a form of the search
//! window, the threshold and the allowed gap, the falling-drift check box,
//! OK and Cancel.

use callisto_core::analysis::ridge::RidgeSettings;
use egui::{Id, Ui, vec2};

use crate::fonts::Weight;
use crate::theme::Palette;
use crate::ui::qlayout::{BoxLayout, FormLayout, Item, Layout, Policy, Size, WidgetSpec};
use crate::ui::{qt, widgets};

const SEARCH: &str = "Search window (channels):";
const THRESHOLD: &str = "Threshold (\u{3c3}):";
const GAP: &str = "Allowed gap (columns):";
const FALLING: &str = "Only follow drift to lower frequency";

pub struct RidgeSettingsDialog {
    search: i32,
    threshold: f64,
    gap: i32,
    falling: bool,
    /// The first field takes the focus when the dialog opens.
    first_frame: bool,
}

impl RidgeSettingsDialog {
    pub fn new(s: &RidgeSettings) -> Self {
        Self { search: s.search_channels as i32, threshold: s.threshold_sigma, gap: s.max_gap as i32, falling: s.falling_only, first_frame: true }
    }

    fn settings(&self) -> RidgeSettings {
        RidgeSettings {
            search_channels: self.search as usize,
            threshold_sigma: self.threshold,
            max_gap: self.gap as usize,
            falling_only: self.falling,
        }
    }

    fn layout(ui: &Ui) -> Layout {
        let check_w = widgets::text_width(ui, FALLING, qt::FONT_PX, Weight::Regular).ceil() as i32 + 24;
        // QDialogButtonBox: the buttons at the right, 6 px apart.
        let buttons = BoxLayout::h([0; 4], 6)
            .stretch(1)
            .widget(qt::button_spec(ui, "ok", "OK", false))
            .widget(qt::button_spec(ui, "cancel", "Cancel", false));
        FormLayout::new([11; 4], 6, 6)
            .row(qt::label_spec(ui, "search_label", SEARCH), Item::Widget(qt::spin_spec(ui, "search", "100")))
            .row(qt::label_spec(ui, "threshold_label", THRESHOLD), Item::Widget(qt::spin_spec(ui, "threshold", "50.0")))
            .row(qt::label_spec(ui, "gap_label", GAP), Item::Widget(qt::spin_spec(ui, "gap", "1000")))
            .span(Item::Widget(WidgetSpec::new("falling", Size::new(check_w, 17), Policy::Minimum, Policy::Fixed)))
            .span(buttons.into_item())
            .into()
    }

    /// The contents: `Some(Some(settings))` for OK, `Some(None)` for Cancel.
    pub fn frame(&mut self, ui: &mut Ui, p: &Palette) -> Option<Option<RidgeSettings>> {
        qt::with_app_style(|| self.contents(ui, p))
    }

    fn contents(&mut self, ui: &mut Ui, p: &Palette) -> Option<Option<RidgeSettings>> {
        let r = ui.max_rect();
        let g = Self::layout(ui).layout_window(Size::new(r.width().round() as i32, r.height().round() as i32));
        let rect = |id: &str| g.rect(id).to_egui(r.min);
        for (id, text) in [("search_label", SEARCH), ("threshold_label", THRESHOLD), ("gap_label", GAP)] {
            let lr = rect(id);
            widgets::text_left(ui, lr.left(), lr.center().y, text, qt::FONT_PX, Weight::Regular, p.text);
        }
        if std::mem::take(&mut self.first_frame) {
            ui.memory_mut(|m| m.request_focus(Id::new("ridge_search").with("edit")));
        }
        qt::spin_box(ui, rect("search"), Id::new("ridge_search"), &mut self.search, (1, 100), "", "", true, p);
        qt::double_spin_box(ui, rect("threshold"), Id::new("ridge_threshold"), &mut self.threshold, (0.0, 50.0), 0.5, 1, "", false, true, p);
        qt::spin_box(ui, rect("gap"), Id::new("ridge_gap"), &mut self.gap, (0, 1000), "", "", true, p);
        qt::check_box(ui, rect("falling"), Id::new("ridge_falling"), FALLING, &mut self.falling, true, p);
        if qt::button(ui, rect("ok"), Id::new("ridge_ok"), "OK", false, true, p).clicked() || ui.input(|i| i.key_pressed(egui::Key::Enter)) {
            return Some(Some(self.settings()));
        }
        if qt::button(ui, rect("cancel"), Id::new("ridge_cancel"), "Cancel", false, true, p).clicked()
            || ui.input(|i| i.key_pressed(egui::Key::Escape))
        {
            return Some(None);
        }
        None
    }

    /// Opens at the form's size hint (291 × 244 in the full app).
    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> Option<Option<RidgeSettings>> {
        let size = vec2(291.0, 244.0);
        crate::ui::dialogs::dialog_window(ctx, "ridge_settings", "Ridge Tracking Settings", size, p, None, |ui| self.frame(ui, p))
    }
}
