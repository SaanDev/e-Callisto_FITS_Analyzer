//! The analysis tools' dialogs: Plot Light Curve (`LightCurveFrequencyDialog`),
//! Light Curve Settings (`LightCurveSettingsDialog`) and the text label
//! dialog (`TextAnnotationDialog`), from `src/ui/radio/dialogs/`.
//!
//! Each lays its widgets out with Qt's layouts (`super::qlayout`) from the
//! same size hints the full app's widgets have, so the windows come out the
//! same size with the same rectangles. `frame` draws a dialog into a `Ui`
//! (its window, or a test harness); `show` opens it as its own window.

use callisto_core::analysis::annotations::{DEFAULT_ANNOTATION_COLOR, TextStyle};
use callisto_core::analysis::light_curve::LightCurveSettings;
use callisto_core::plot::qt_raster::{color_name, parse_color};
use egui::{Color32, Id, Rect, Ui, vec2};

use super::color_dialog::ColorDialog;
use super::qlayout::{BoxLayout, Container, FormLayout, Geometry, Item, Layout, Policy, Size, WidgetSpec};
use super::qt;
use super::widgets::{self, text_width};
use crate::fonts::Weight;
use crate::session::{Notice, NoticeKind};
use crate::theme::Palette;

/// `QCheckBox` size hint: the 17 px indicator, its spacing and the text.
fn check_spec(ui: &Ui, id: &'static str, text: &str) -> WidgetSpec {
    let w = text_width(ui, text, qt::FONT_PX, Weight::Regular).ceil() as i32 + 24;
    WidgetSpec::new(id, Size::new(w, 17), Policy::Minimum, Policy::Fixed)
}

/// A row of check boxes in a holder widget (`_pack_row`): 10 px apart, then a stretch.
fn check_row(id: &'static str, specs: Vec<WidgetSpec>) -> Item {
    let mut row = BoxLayout::h([0; 4], 10);
    for s in specs {
        row = row.widget(s);
    }
    let layout = row.stretch(1);
    let hint = Layout::from(layout.clone()).size_hint();
    Item::Container(Container {
        spec: WidgetSpec::new(id, hint, Policy::Preferred, Policy::Preferred),
        margins: [0; 4],
        layout: Box::new(layout.into()),
    })
}

/// The longest text a `QDoubleSpinBox` shows over its range.
fn spin_sample(range: (f64, f64), decimals: usize, suffix: &str) -> String {
    let a = format!("{:.decimals$}{suffix}", range.0);
    let b = format!("{:.decimals$}{suffix}", range.1);
    if b.len() >= a.len() { b } else { a }
}

/// Lays `layout` out in `ui`'s window and returns the rectangles.
fn place(ui: &Ui, layout: &Layout) -> (Geometry, egui::Pos2) {
    let r = ui.max_rect();
    let size = Size::new(r.width().round() as i32, r.height().round() as i32);
    (layout.layout_window(size), r.min)
}

fn rect(g: &Geometry, o: egui::Pos2, id: &str) -> Rect {
    g.rect(id).to_egui(o)
}

/// A `QLabel` placed by a form layout: text left, vertically centred.
fn form_label(ui: &Ui, g: &Geometry, o: egui::Pos2, id: &str, text: &str, p: &Palette) {
    let r = rect(g, o, id);
    widgets::text_left(ui, r.left(), r.center().y, text, qt::FONT_PX, Weight::Regular, p.text);
}

/// Resizes the dialog's window to `want` once it is known.
fn fit_window(ui: &Ui, size: &mut Option<egui::Vec2>, want: egui::Vec2) {
    if *size != Some(want) {
        *size = Some(want);
        ui.ctx().send_viewport_cmd(egui::ViewportCommand::InnerSize(want));
    }
}

/// The window size a dialog opens at: `resize(w, h)` bounded below by the
/// layout's minimum size, or the size hint when not resized.
fn window_size(layout: &Layout, resize: Option<(i32, i32)>) -> egui::Vec2 {
    let sum = layout.summary();
    let (w, h) = match resize {
        Some((w, h)) => (w.max(sum.min.w), h.max(sum.min.h)),
        None => (sum.hint.w, sum.hint.h),
    };
    vec2(w as f32, h as f32)
}

// ------------------------------------------------------------ frequency

/// Analysis → Plot Light Curves → Enter frequency.
pub struct FrequencyDialog {
    lo: f64,
    hi: f64,
    value: f64,
    first_frame: bool,
    size: Option<egui::Vec2>,
}

impl FrequencyDialog {
    pub fn new(min_mhz: f64, max_mhz: f64, initial: Option<f64>) -> Self {
        let lo = min_mhz.min(max_mhz);
        let mut hi = min_mhz.max(max_mhz);
        if hi <= lo {
            hi = lo + 1e-6;
        }
        let value = initial.map_or((lo + hi) / 2.0, |v| v.clamp(lo, hi));
        Self { lo, hi, value, first_frame: true, size: None }
    }

    fn layout(&self, ui: &Ui) -> Layout {
        let range = format!("Available range: {:.3} to {:.3} MHz", self.lo, self.hi);
        let spin = qt::spin_spec(ui, "frequency", &spin_sample((self.lo, self.hi), 3, " MHz"));
        let form =
            FormLayout::new([0; 4], 6, 6).row(qt::label_spec(ui, "frequency_label", "Frequency"), Item::Widget(spin));
        let buttons = BoxLayout::h([0; 4], 6)
            .stretch(1)
            .widget(qt::button_spec(ui, "plot", "Plot", false))
            .widget(qt::button_spec(ui, "cancel", "Cancel", false));
        BoxLayout::v([11; 4], 6)
            .widget(qt::label_spec(ui, "range", &range))
            .add(form.into_item())
            .add(buttons.into_item())
            .into()
    }

    /// The contents; `Some(Some(MHz))` for Plot, `Some(None)` for Cancel.
    pub fn frame(&mut self, ui: &mut Ui, p: &Palette) -> Option<Option<f64>> {
        qt::with_app_style(|| self.contents(ui, p))
    }

    fn contents(&mut self, ui: &mut Ui, p: &Palette) -> Option<Option<f64>> {
        let layout = self.layout(ui);
        let (g, o) = place(ui, &layout);
        let range = format!("Available range: {:.3} to {:.3} MHz", self.lo, self.hi);
        form_label(ui, &g, o, "range", &range, p);
        form_label(ui, &g, o, "frequency_label", "Frequency", p);
        let step = ((self.hi - self.lo) / 100.0).max(0.001);
        let first = std::mem::take(&mut self.first_frame);
        let id = Id::new("lc_frequency");
        qt::double_spin_box(
            ui,
            rect(&g, o, "frequency"),
            id,
            &mut self.value,
            (self.lo, self.hi),
            step,
            3,
            " MHz",
            first,
            true,
            p,
        );
        let enter = ui.input(|i| i.key_pressed(egui::Key::Enter));
        if qt::button(ui, rect(&g, o, "plot"), Id::new("lc_plot"), "Plot", false, true, p).clicked() || enter {
            return Some(Some(self.value));
        }
        if qt::button(ui, rect(&g, o, "cancel"), Id::new("lc_cancel"), "Cancel", false, true, p).clicked()
            || ui.input(|i| i.key_pressed(egui::Key::Escape))
        {
            return Some(None);
        }
        None
    }

    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> Option<Option<f64>> {
        let size = self.size.unwrap_or(vec2(252.0, 141.0));
        super::dialogs::dialog_window(ctx, "lc_frequency", "Plot Light Curve", size, p, None, |ui| {
            let want = window_size(&self.layout(ui), None);
            fit_window(ui, &mut self.size, want);
            self.frame(ui, p)
        })
    }
}

// ------------------------------------------------------------- settings

/// What the Light Curve Settings window asks of the main window.
pub enum SettingsAction {
    /// `settingsChanged`: apply these at once.
    Changed(LightCurveSettings),
    Close,
}

/// Analysis → Plot Light Curves → Settings... (not modal).
pub struct LightCurveSettingsDialog {
    settings: LightCurveSettings,
    color: Option<ColorDialog>,
}

const STYLES: [&str; 3] = ["solid", "dashed", "dotted"];

impl LightCurveSettingsDialog {
    pub fn new(settings: &LightCurveSettings) -> Self {
        Self { settings: settings.clone().normalized(), color: None }
    }

    /// `load_settings`, when the main window's settings change.
    pub fn load(&mut self, settings: &LightCurveSettings) {
        self.settings = settings.clone().normalized();
    }

    fn layout(&self, ui: &Ui) -> Layout {
        let color = &self.settings.line_color;
        let color_spec = WidgetSpec::new(
            "color",
            Size::new(text_width(ui, color, qt::FONT_PX, Weight::Regular).ceil() as i32 + 16, 42),
            Policy::Minimum,
            Policy::Fixed,
        )
        .min_height(42);
        let form = FormLayout::new([0; 4], 8, 8)
            .row(
                qt::label_spec(ui, "mode_label", "Mode"),
                check_row(
                    "mode",
                    vec![
                        check_spec(ui, "single", "Single light curve"),
                        check_spec(ui, "multiple", "Multiple light curves"),
                    ],
                ),
            )
            .row(qt::label_spec(ui, "color_label", "Color"), Item::Widget(color_spec))
            .row(qt::label_spec(ui, "width_label", "Thickness"), Item::Widget(qt::spin_spec(ui, "width", "12.0")))
            .row(qt::label_spec(ui, "scale_label", "Vertical Scale"), Item::Widget(qt::spin_spec(ui, "scale", "5.0")))
            .row(qt::label_spec(ui, "opacity_label", "Opacity"), Item::Widget(qt::spin_spec(ui, "opacity", "1.00")))
            .row(qt::label_spec(ui, "style_label", "Line Style"), Item::Widget(qt::combo_spec(ui, "style", &STYLES)))
            .row(
                qt::label_spec(ui, "labels_label", "Labels"),
                Item::Widget(check_spec(ui, "labels", "Show frequency labels")),
            );
        let buttons = BoxLayout::h([0; 4], 10)
            .stretch(1)
            .widget(qt::button_spec(ui, "apply", "Apply", false))
            .widget(qt::button_spec(ui, "reset", "Reset to Defaults", false))
            .widget(qt::button_spec(ui, "close", "Close", false));
        BoxLayout::v([14; 4], 10).add(form.into_item()).stretch(1).add(buttons.into_item()).into()
    }

    pub fn frame(&mut self, ui: &mut Ui, p: &Palette) -> Option<SettingsAction> {
        qt::with_app_style(|| self.contents(ui, p))
    }

    fn contents(&mut self, ui: &mut Ui, p: &Palette) -> Option<SettingsAction> {
        let layout = self.layout(ui);
        let (g, o) = place(ui, &layout);
        let before = self.settings.clone();
        for (id, text) in [
            ("mode_label", "Mode"),
            ("color_label", "Color"),
            ("width_label", "Thickness"),
            ("scale_label", "Vertical Scale"),
            ("opacity_label", "Opacity"),
            ("style_label", "Line Style"),
            ("labels_label", "Labels"),
        ] {
            form_label(ui, &g, o, id, text, p);
        }
        let s = &mut self.settings;
        let mut single = !s.multiple;
        let mut multiple = s.multiple;
        // _on_mode_toggled: the two act as one choice that cannot be cleared.
        if qt::check_box(ui, rect(&g, o, "single"), Id::new("lcs_single"), "Single light curve", &mut single, true, p) {
            s.multiple = false;
        }
        if qt::check_box(
            ui,
            rect(&g, o, "multiple"),
            Id::new("lcs_multiple"),
            "Multiple light curves",
            &mut multiple,
            true,
            p,
        ) {
            s.multiple = true;
        }
        if color_button(ui, rect(&g, o, "color"), Id::new("lcs_color"), &s.line_color, p) {
            let c = parse_color(&s.line_color).unwrap_or([0x00, 0xe5, 0xff, 255]);
            self.color = Some(ColorDialog::new("Choose light curve color", c));
        }
        let s = &mut self.settings;
        qt::double_spin_box(
            ui,
            rect(&g, o, "width"),
            Id::new("lcs_width"),
            &mut s.line_width,
            (0.5, 12.0),
            0.5,
            1,
            "",
            false,
            true,
            p,
        );
        qt::double_spin_box(
            ui,
            rect(&g, o, "scale"),
            Id::new("lcs_scale"),
            &mut s.vertical_scale,
            (0.1, 5.0),
            0.1,
            1,
            "",
            false,
            true,
            p,
        );
        qt::double_spin_box(
            ui,
            rect(&g, o, "opacity"),
            Id::new("lcs_opacity"),
            &mut s.opacity,
            (0.1, 1.0),
            0.05,
            2,
            "",
            false,
            true,
            p,
        );
        let items: Vec<String> = STYLES.iter().map(|s| (*s).to_owned()).collect();
        let current = STYLES.iter().position(|st| *st == s.line_style);
        if let Some(i) = qt::combo(ui, rect(&g, o, "style"), Id::new("lcs_style"), &items, current, "", true, p) {
            s.line_style = STYLES[i].into();
        }
        let mut labels = s.show_frequency_label;
        if qt::check_box(
            ui,
            rect(&g, o, "labels"),
            Id::new("lcs_labels"),
            "Show frequency labels",
            &mut labels,
            true,
            p,
        ) {
            s.show_frequency_label = labels;
        }
        let mut action = None;
        if qt::button(ui, rect(&g, o, "apply"), Id::new("lcs_apply"), "Apply", false, true, p).clicked() {
            action = Some(SettingsAction::Changed(self.settings.clone().normalized()));
        }
        if qt::button(ui, rect(&g, o, "reset"), Id::new("lcs_reset"), "Reset to Defaults", false, true, p).clicked() {
            self.settings = LightCurveSettings::default();
        }
        if qt::button(ui, rect(&g, o, "close"), Id::new("lcs_close"), "Close", false, true, p).clicked() {
            return Some(SettingsAction::Close);
        }
        if let Some(dialog) = &mut self.color
            && let Some(result) = dialog.show(ui.ctx(), p)
        {
            if let Some(c) = result {
                self.settings.line_color = color_name(c);
            }
            self.color = None;
        }
        if self.settings != before {
            // settingsChanged on every control change.
            self.settings = self.settings.clone().normalized();
            action = Some(SettingsAction::Changed(self.settings.clone()));
        }
        action
    }

    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> Option<SettingsAction> {
        let mut action = None;
        crate::downloads::window(
            ctx,
            "lc_settings",
            "Light Curve Settings",
            vec2(420.0, 376.0),
            vec2(364.0, 376.0),
            p,
            |ui, close| {
                action = self.frame(ui, p);
                if close {
                    action = Some(SettingsAction::Close);
                }
            },
        );
        action
    }
}

/// `_ColorButton`: the colour's name on the colour, light or dark text by
/// lightness, a grey border. Returns true when clicked.
fn color_button(ui: &Ui, r: Rect, id: Id, name: &str, p: &Palette) -> bool {
    let c = parse_color(name).unwrap_or([0x00, 0xe5, 0xff, 255]);
    let fill = Color32::from_rgb(c[0], c[1], c[2]);
    let fg =
        if hsl_lightness(c) > 150 { Color32::from_rgb(0x10, 0x10, 0x10) } else { Color32::from_rgb(0xf5, 0xf5, 0xf5) };
    let resp = ui.interact(r, id, egui::Sense::click());
    widgets::qt_box(ui, r, 10.0, fill, Color32::from_rgb(0x80, 0x80, 0x80));
    widgets::text_center(ui, r.center(), name, qt::FONT_PX, Weight::Regular, fg);
    let _ = p;
    resp.clicked()
}

/// `QColor::lightness`.
fn hsl_lightness(c: [u8; 4]) -> i32 {
    let (max, min) = (c[0].max(c[1]).max(c[2]) as i32, c[0].min(c[1]).min(c[2]) as i32);
    (max + min) / 2
}

// ------------------------------------------------------------------ text

/// What the text label dialog is for.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TextPurpose {
    Add,
    Edit(usize),
}

/// `TextAnnotationDialog`: text, font, size, style and colour.
pub struct TextAnnotationDialog {
    pub title: String,
    pub purpose: TextPurpose,
    text: String,
    family: String,
    families: Vec<String>,
    size: i32,
    bold: bool,
    italic: bool,
    color: String,
    color_dialog: Option<ColorDialog>,
    notice: Option<Notice>,
    window: Option<egui::Vec2>,
    first_frame: bool,
}

impl TextAnnotationDialog {
    pub fn new(
        title: &str,
        purpose: TextPurpose,
        text: &str,
        style: &TextStyle,
        ui_family: &str,
        families: Vec<String>,
    ) -> Self {
        let family = if style.font_family.trim().is_empty() {
            ui_family.to_owned()
        } else {
            style.font_family.trim().to_owned()
        };
        let color = parse_color(&style.color).map_or_else(|| DEFAULT_ANNOTATION_COLOR.to_owned(), color_name);
        Self {
            title: title.into(),
            purpose,
            text: text.into(),
            family,
            families,
            size: style.font_size.clamp(6, 72) as i32,
            bold: style.font_bold,
            italic: style.font_italic,
            color,
            color_dialog: None,
            notice: None,
            window: None,
            first_frame: true,
        }
    }

    /// `payload`.
    pub fn style(&self) -> TextStyle {
        TextStyle {
            color: self.color.clone(),
            font_family: self.family.trim().to_owned(),
            font_size: self.size as i64,
            font_bold: self.bold,
            font_italic: self.italic,
        }
    }

    fn layout(&self, ui: &Ui) -> Layout {
        let code_w = text_width(ui, &self.color, qt::FONT_PX, Weight::Regular).ceil() as i32;
        let color_row = BoxLayout::h([0; 4], 8)
            .add_stretched(
                Item::Widget(
                    WidgetSpec::new("preview", Size::new(54, 24), Policy::Preferred, Policy::Preferred)
                        .min_size(Size::new(54, 24))
                        .max_width(54),
                ),
                0,
            )
            .add_stretched(
                Item::Widget(WidgetSpec::new("code", Size::new(code_w, 16), Policy::Preferred, Policy::Preferred)),
                1,
            )
            .add_stretched(Item::Widget(qt::button_spec(ui, "choose", "Choose...", false)), 0);
        let hint = Layout::from(color_row.clone()).size_hint();
        let color_holder = Item::Container(Container {
            spec: WidgetSpec::new("color_row", hint, Policy::Preferred, Policy::Preferred),
            margins: [0; 4],
            layout: Box::new(color_row.into()),
        });
        let font = WidgetSpec::new("font", Size::new(140, 42), Policy::Preferred, Policy::Fixed)
            .min_hint(Size::new(274, 42))
            .min_height(42);
        let form = FormLayout::new([0; 4], 8, 8)
            .row(qt::label_spec(ui, "text_label", "Text"), Item::Widget(qt::line_edit_spec("text")))
            .row(qt::label_spec(ui, "font_label", "Font"), Item::Widget(font))
            .row(qt::label_spec(ui, "size_label", "Font Size"), Item::Widget(qt::spin_spec(ui, "size", "72")))
            .row(
                qt::label_spec(ui, "style_label", "Font Style"),
                check_row("style", vec![check_spec(ui, "bold", "Bold"), check_spec(ui, "italic", "Italic")]),
            )
            .row(qt::label_spec(ui, "color_label", "Color"), color_holder);
        let buttons = BoxLayout::h([0; 4], 6)
            .stretch(1)
            .widget(qt::button_spec(ui, "ok", "OK", false))
            .widget(qt::button_spec(ui, "cancel", "Cancel", false));
        BoxLayout::v([14; 4], 10).add(form.into_item()).add(buttons.into_item()).into()
    }

    /// `Some(Some((text, style)))` on OK, `Some(None)` on Cancel.
    pub fn frame(&mut self, ui: &mut Ui, p: &Palette) -> Option<Option<(String, TextStyle)>> {
        qt::with_app_style(|| self.contents(ui, p))
    }

    fn contents(&mut self, ui: &mut Ui, p: &Palette) -> Option<Option<(String, TextStyle)>> {
        let layout = self.layout(ui);
        let (g, o) = place(ui, &layout);
        for (id, text) in [
            ("text_label", "Text"),
            ("font_label", "Font"),
            ("size_label", "Font Size"),
            ("style_label", "Font Style"),
            ("color_label", "Color"),
        ] {
            form_label(ui, &g, o, id, text, p);
        }
        if std::mem::take(&mut self.first_frame) {
            ui.memory_mut(|m| m.request_focus(Id::new("ta_text")));
        }
        qt::line_edit(ui, rect(&g, o, "text"), Id::new("ta_text"), &mut self.text, "Annotation text", true, p);
        qt::editable_combo(ui, rect(&g, o, "font"), Id::new("ta_font"), &mut self.family, &self.families, true, p);
        qt::spin_box(ui, rect(&g, o, "size"), Id::new("ta_size"), &mut self.size, (6, 72), "", "", true, p);
        qt::check_box(ui, rect(&g, o, "bold"), Id::new("ta_bold"), "Bold", &mut self.bold, true, p);
        qt::check_box(ui, rect(&g, o, "italic"), Id::new("ta_italic"), "Italic", &mut self.italic, true, p);
        // _update_color_preview: a swatch, and the name on the colour.
        let c = parse_color(&self.color).unwrap_or([0x00, 0xd4, 0xff, 255]);
        let fill = Color32::from_rgb(c[0], c[1], c[2]);
        widgets::qt_box(ui, rect(&g, o, "preview"), 0.0, fill, Color32::from_rgb(0x88, 0x88, 0x88));
        let code = rect(&g, o, "code");
        let fg = if hsl_lightness(c) > 140 {
            Color32::from_rgb(0x10, 0x10, 0x10)
        } else {
            Color32::from_rgb(0xf5, 0xf5, 0xf5)
        };
        let w = text_width(ui, &self.color, qt::FONT_PX, Weight::Regular);
        let band = Rect::from_min_size(egui::pos2(code.left(), code.center().y - 8.0), vec2(w, 16.0));
        ui.painter().rect_filled(band, 0.0, fill);
        widgets::text_left(ui, code.left(), code.center().y, &self.color, qt::FONT_PX, Weight::Regular, fg);
        if qt::button(ui, rect(&g, o, "choose"), Id::new("ta_choose"), "Choose...", false, true, p).clicked() {
            self.color_dialog = Some(ColorDialog::new("Choose Text Color", c));
        }
        let mut out = None;
        if qt::button(ui, rect(&g, o, "ok"), Id::new("ta_ok"), "OK", false, true, p).clicked() {
            if self.text.trim().is_empty() {
                self.notice = Some(Notice {
                    kind: NoticeKind::Information,
                    title: "Text Annotation".into(),
                    text: "Enter text for the label.".into(),
                });
            } else {
                out = Some(Some((self.text.trim().to_owned(), self.style())));
            }
        }
        if qt::button(ui, rect(&g, o, "cancel"), Id::new("ta_cancel"), "Cancel", false, true, p).clicked()
            || ui.input(|i| i.key_pressed(egui::Key::Escape))
        {
            out = Some(None);
        }
        if let Some(dialog) = &mut self.color_dialog
            && let Some(result) = dialog.show(ui.ctx(), p)
        {
            if let Some(c) = result {
                self.color = color_name(c);
            }
            self.color_dialog = None;
        }
        if let Some(n) = &self.notice
            && super::dialogs::message_box(ui.ctx(), n, p)
        {
            self.notice = None;
        }
        out
    }

    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> Option<Option<(String, TextStyle)>> {
        let id = format!("text_annotation:{}", self.title);
        let title = self.title.clone();
        let size = self.window.unwrap_or(vec2(420.0, 305.0));
        super::dialogs::dialog_window(ctx, &id, &title, size, p, None, |ui| {
            // resize(420, 260), bounded by the layout's minimum.
            let want = window_size(&self.layout(ui), Some((420, 260)));
            fit_window(ui, &mut self.window, want);
            self.frame(ui, p)
        })
    }
}
