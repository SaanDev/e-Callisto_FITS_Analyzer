//! Type II band-splitting → Settings (`TypeIIGraphSettingsDialog`): the
//! graph's fonts and the lanes' and B-vs-R colours, widths and sizes. Every
//! change previews on the plot; Apply keeps it, Close drops what was not
//! applied.

use callisto_core::analysis::session::TypeIIPlotStyle;
use egui::{Color32, Id, Rect, Ui, vec2};

use crate::downloads::{at, lay_out};
use crate::fonts::Weight;
use crate::theme::Palette;
use crate::ui::color_dialog::ColorDialog;
use crate::ui::qlayout::{BoxLayout, Container, FormLayout, Item, Layout, Policy, Size, WidgetSpec};
use crate::ui::{qt, widgets};

/// What the dialog tells the window.
#[derive(Clone, Debug, PartialEq)]
pub enum SettingsEvent {
    /// `previewStyleChanged`.
    Preview(TypeIIPlotStyle),
    /// `styleApplied`.
    Applied(TypeIIPlotStyle),
    Closed,
}

/// The three series groups: (title, key prefix, line label, marker label).
const SERIES: [(&str, &str, &str); 3] = [("Upper Band", "Fit Line", "Picked Points"), ("Lower Band", "Fit Line", "Picked Points"), ("B vs R", "Fit Line", "Markers")];
const SERIES_IDS: [[&str; 6]; 3] = [
    ["u_line_label", "u_line_color", "u_width_tag", "u_width", "u_marker_label", "u_marker_color"],
    ["l_line_label", "l_line_color", "l_width_tag", "l_width", "l_marker_label", "l_marker_color"],
    ["b_line_label", "b_line_color", "b_width_tag", "b_width", "b_marker_label", "b_marker_color"],
];
const SIZE_IDS: [[&str; 2]; 3] = [["u_size_tag", "u_size"], ["l_size_tag", "l_size"], ["b_size_tag", "b_size"]];
const GROUP_IDS: [&str; 3] = ["upper_group", "lower_group", "bvr_group"];
const HOLDER_IDS: [[&str; 2]; 3] = [["u_line_row", "u_marker_row"], ["l_line_row", "l_marker_row"], ["b_line_row", "b_marker_row"]];

/// A series' controls: line colour, line width, marker colour, marker size.
#[derive(Clone, Debug, PartialEq)]
struct SeriesControls {
    line_color: String,
    line_width: i32,
    marker_color: String,
    marker_size: i32,
}

pub struct TypeIISettingsDialog {
    applied: TypeIIPlotStyle,
    defaults: TypeIIPlotStyle,
    ui_family: String,
    families: Vec<String>,
    family: String,
    sizes: [i32; 3],
    bold: [bool; 3],
    italic: [bool; 3],
    series: [SeriesControls; 3],
    /// The colour being chosen: (series, marker?) and its dialog.
    picking: Option<(usize, bool, ColorDialog)>,
    first_frame: bool,
    focus_due: bool,
}

/// `QColor(value)`, or white.
fn color_or_white(v: &str) -> [u8; 4] {
    callisto_core::plot::qt_raster::parse_color(v).unwrap_or([255, 255, 255, 255])
}

/// `QColor.name()`: `#rrggbb`, lower case.
fn color_name(c: [u8; 4]) -> String {
    format!("#{:02x}{:02x}{:02x}", c[0], c[1], c[2])
}

impl TypeIISettingsDialog {
    pub fn new(initial: &TypeIIPlotStyle, defaults: &TypeIIPlotStyle, ui_family: &str, families: Vec<String>) -> Self {
        let mut d = Self {
            applied: initial.clone(),
            defaults: defaults.clone(),
            ui_family: ui_family.to_owned(),
            families,
            family: String::new(),
            sizes: [14, 12, 11],
            bold: [false; 3],
            italic: [false; 3],
            series: std::array::from_fn(|_| SeriesControls { line_color: String::new(), line_width: 2, marker_color: String::new(), marker_size: 9 }),
            picking: None,
            first_frame: true,
            focus_due: true,
        };
        d.set_controls(&initial.clone());
        d
    }

    pub fn focus(&mut self) {
        self.focus_due = true;
    }

    /// `load_applied_style`.
    pub fn load_applied_style(&mut self, style: &TypeIIPlotStyle) {
        self.applied = style.clone();
        self.set_controls(&style.clone());
    }

    /// `_set_controls_from_style`.
    fn set_controls(&mut self, s: &TypeIIPlotStyle) {
        self.family = if s.font_family.trim().is_empty() { self.ui_family.clone() } else { s.font_family.trim().to_owned() };
        self.sizes = [s.title_font_px as i32, s.axis_label_font_px as i32, s.tick_font_px as i32];
        self.bold = [s.title_bold, s.axis_bold, s.ticks_bold];
        self.italic = [s.title_italic, s.axis_italic, s.ticks_italic];
        let c = |v: &str| color_name(color_or_white(v));
        self.series = [
            SeriesControls { line_color: c(&s.upper_line_color), line_width: s.upper_line_width as i32, marker_color: c(&s.upper_marker_color), marker_size: s.upper_marker_size as i32 },
            SeriesControls { line_color: c(&s.lower_line_color), line_width: s.lower_line_width as i32, marker_color: c(&s.lower_marker_color), marker_size: s.lower_marker_size as i32 },
            SeriesControls { line_color: c(&s.bvr_line_color), line_width: s.bvr_line_width as i32, marker_color: c(&s.bvr_marker_color), marker_size: s.bvr_marker_size as i32 },
        ];
    }

    /// `current_style`: the app's own family stays "" unless one was applied.
    pub fn current_style(&self) -> TypeIIPlotStyle {
        let mut family = crate::fonts::normalize_font_family(self.family.trim());
        if family == self.ui_family && self.applied.font_family.trim().is_empty() {
            family = String::new();
        }
        let [u, l, b] = &self.series;
        TypeIIPlotStyle {
            font_family: family,
            title_font_px: self.sizes[0] as i64,
            axis_label_font_px: self.sizes[1] as i64,
            tick_font_px: self.sizes[2] as i64,
            title_bold: self.bold[0],
            title_italic: self.italic[0],
            axis_bold: self.bold[1],
            axis_italic: self.italic[1],
            ticks_bold: self.bold[2],
            ticks_italic: self.italic[2],
            upper_line_color: u.line_color.clone(),
            upper_marker_color: u.marker_color.clone(),
            upper_line_width: u.line_width as i64,
            upper_marker_size: u.marker_size as i64,
            lower_line_color: l.line_color.clone(),
            lower_marker_color: l.marker_color.clone(),
            lower_line_width: l.line_width as i64,
            lower_marker_size: l.marker_size as i64,
            bvr_line_color: b.line_color.clone(),
            bvr_marker_color: b.marker_color.clone(),
            bvr_line_width: b.line_width as i64,
            bvr_marker_size: b.marker_size as i64,
        }
    }

    fn color_spec(ui: &Ui, id: &'static str, text: &str) -> WidgetSpec {
        // `padding: 4px 8px` and a 1 px border; the app's min-height 32.
        let w = widgets::text_width(ui, text, qt::FONT_PX, Weight::Regular).ceil() as i32 + 18;
        WidgetSpec::new(id, Size::new(w, 42), Policy::Minimum, Policy::Fixed).min_height(42)
    }

    fn check_spec(ui: &Ui, id: &'static str, text: &str) -> WidgetSpec {
        let w = widgets::text_width(ui, text, qt::FONT_PX, Weight::Regular).ceil() as i32 + 24;
        WidgetSpec::new(id, Size::new(w, 17), Policy::Minimum, Policy::Fixed)
    }

    fn layout(&self, ui: &Ui) -> Layout {
        // `_pack_row`: a holder widget, its widgets 8 px apart, then a stretch.
        let holder = |id: &'static str, l: BoxLayout| {
            Item::Container(Container { spec: WidgetSpec::new(id, Size::new(0, 0), Policy::Preferred, Policy::Preferred), margins: [0; 4], layout: Box::new(l.stretch(1).into()) })
        };
        let group = |id: &'static str, l: Layout| {
            Item::Container(Container { spec: WidgetSpec::new(id, Size::new(0, 0), Policy::Preferred, Policy::Preferred), margins: [13, 27, 13, 13], layout: Box::new(l) })
        };
        let font_row = |prefix: [&'static str; 4]| {
            holder(
                prefix[0],
                BoxLayout::h([0; 4], 8)
                    .widget(qt::spin_spec(ui, prefix[1], "48"))
                    .widget(Self::check_spec(ui, prefix[2], "Bold"))
                    .widget(Self::check_spec(ui, prefix[3], "Italic")),
            )
        };
        let text = FormLayout::new([10; 4], 8, 8)
            .row(qt::label_spec(ui, "family_label", "Font Family"), Item::Widget(qt::combo_len_spec("family", 10)))
            .row(qt::label_spec(ui, "title_label", "Title"), font_row(["title_row", "title_px", "title_bold", "title_italic"]))
            .row(qt::label_spec(ui, "axis_label", "Axis Labels"), font_row(["axis_row", "axis_px", "axis_bold", "axis_italic"]))
            .row(qt::label_spec(ui, "tick_label", "Tick Labels"), font_row(["tick_row", "tick_px", "tick_bold", "tick_italic"]));
        let mut root = BoxLayout::v([14; 4], 10).add(group("text_group", text.into()));
        for (i, (_, line_label, marker_label)) in SERIES.iter().enumerate() {
            let ids = SERIES_IDS[i];
            let s = &self.series[i];
            let form = FormLayout::new([10; 4], 8, 8)
                .row(
                    qt::label_spec(ui, ids[0], line_label),
                    holder(
                        HOLDER_IDS[i][0],
                        BoxLayout::h([0; 4], 8)
                            .widget(Self::color_spec(ui, ids[1], &s.line_color))
                            .widget(qt::label_spec(ui, ids[2], "Width"))
                            .widget(qt::spin_spec(ui, ids[3], "12")),
                    ),
                )
                .row(
                    qt::label_spec(ui, ids[4], marker_label),
                    holder(
                        HOLDER_IDS[i][1],
                        BoxLayout::h([0; 4], 8)
                            .widget(Self::color_spec(ui, ids[5], &s.marker_color))
                            .widget(qt::label_spec(ui, SIZE_IDS[i][0], "Size"))
                            .widget(qt::spin_spec(ui, SIZE_IDS[i][1], "32")),
                    ),
                );
            root = root.add(group(GROUP_IDS[i], form.into()));
        }
        let buttons = BoxLayout::h([0; 4], 10)
            .stretch(1)
            .widget(qt::button_spec(ui, "apply", "Apply", false))
            .widget(qt::button_spec(ui, "reset", "Reset to Defaults", false))
            .widget(qt::button_spec(ui, "close", "Close", false));
        root.stretch(1).add(buttons.into_item()).into()
    }

    /// The window size: `resize(460, 640)`, grown to the layout's minimum.
    pub fn window_size(&self, ui: &Ui) -> egui::Vec2 {
        let s = self.layout(ui).summary();
        vec2(460f32.max(s.min.w as f32), 640f32.max(s.min.h as f32))
    }

    pub fn frame(&mut self, ui: &mut Ui, p: &Palette) -> Vec<SettingsEvent> {
        qt::with_app_style(|| self.contents(ui, p))
    }

    fn contents(&mut self, ui: &mut Ui, p: &Palette) -> Vec<SettingsEvent> {
        let mut events = Vec::new();
        let before = self.current_style();
        let layout = self.layout(ui);
        let (g, o) = lay_out(&layout, ui.max_rect());
        let r = |id: &str| at(&g, o, id);
        // QFormLayout gives a label the row's height less the field's padding.
        let form_label = |ui: &Ui, id: &str, text: &str| {
            let lr = r(id);
            qt::label(ui, Rect::from_min_size(lr.min, vec2(lr.width(), lr.height().min(28.0))), text, p.text);
        };
        qt::group_box(ui, r("text_group"), "Text Formatting", p);
        for (id, title) in GROUP_IDS.iter().zip(SERIES.iter().map(|s| s.0)) {
            qt::group_box(ui, r(id), title, p);
        }
        for (id, text) in [("family_label", "Font Family"), ("title_label", "Title"), ("axis_label", "Axis Labels"), ("tick_label", "Tick Labels")] {
            form_label(ui, id, text);
        }
        if std::mem::take(&mut self.first_frame) {
            // The line edit inside the combo takes the focus.
            ui.memory_mut(|m| m.request_focus(Id::new("t2s_family").with("edit")));
        }
        qt::editable_combo(ui, r("family"), Id::new("t2s_family"), &mut self.family, &self.families, true, p);
        for (i, ids) in [["title_px", "title_bold", "title_italic"], ["axis_px", "axis_bold", "axis_italic"], ["tick_px", "tick_bold", "tick_italic"]].iter().enumerate() {
            qt::spin_box(ui, r(ids[0]), Id::new(("t2s_px", i)), &mut self.sizes[i], (6, 48), "", "", true, p);
            qt::check_box(ui, r(ids[1]), Id::new(("t2s_bold", i)), "Bold", &mut self.bold[i], true, p);
            qt::check_box(ui, r(ids[2]), Id::new(("t2s_italic", i)), "Italic", &mut self.italic[i], true, p);
        }
        for i in 0..3 {
            let ids = SERIES_IDS[i];
            form_label(ui, ids[0], SERIES[i].1);
            form_label(ui, ids[4], SERIES[i].2);
            qt::label(ui, r(ids[2]), "Width", p.text);
            qt::label(ui, r(SIZE_IDS[i][0]), "Size", p.text);
            let title = SERIES[i].0;
            let line = self.series[i].line_color.clone();
            if color_button(ui, r(ids[1]), Id::new(("t2s_line_color", i)), &line) {
                self.picking = Some((i, false, ColorDialog::new(&format!("Choose {title} line color"), color_or_white(&line))));
            }
            let marker = self.series[i].marker_color.clone();
            if color_button(ui, r(ids[5]), Id::new(("t2s_marker_color", i)), &marker) {
                self.picking = Some((i, true, ColorDialog::new(&format!("Choose {title} marker color"), color_or_white(&marker))));
            }
            qt::spin_box(ui, r(ids[3]), Id::new(("t2s_width", i)), &mut self.series[i].line_width, (1, 12), "", "", true, p);
            qt::spin_box(ui, r(SIZE_IDS[i][1]), Id::new(("t2s_size", i)), &mut self.series[i].marker_size, (2, 32), "", "", true, p);
        }
        if let Some((i, marker, dialog)) = &mut self.picking
            && let Some(result) = dialog.show(ui.ctx(), p)
        {
            if let Some(c) = result {
                let name = color_name(c);
                if *marker {
                    self.series[*i].marker_color = name;
                } else {
                    self.series[*i].line_color = name;
                }
            }
            self.picking = None;
        }
        let mut close = false;
        if qt::button(ui, r("apply"), Id::new("t2s_apply"), "Apply", false, true, p).clicked() {
            // `_apply`.
            let style = self.current_style();
            self.applied = style.clone();
            events.push(SettingsEvent::Applied(style));
        }
        if qt::button(ui, r("reset"), Id::new("t2s_reset"), "Reset to Defaults", false, true, p).clicked() {
            let defaults = self.defaults.clone();
            self.set_controls(&defaults);
            events.push(SettingsEvent::Preview(self.current_style()));
        }
        if qt::button(ui, r("close"), Id::new("t2s_close"), "Close", false, true, p).clicked() {
            close = true;
        }
        let after = self.current_style();
        if after != before && !events.iter().any(|e| matches!(e, SettingsEvent::Preview(_) | SettingsEvent::Applied(_))) {
            events.push(SettingsEvent::Preview(after));
        }
        if close {
            events.extend(self.close_events());
        }
        events
    }

    /// `closeEvent`: an unapplied preview is taken back.
    fn close_events(&self) -> Vec<SettingsEvent> {
        let mut out = Vec::new();
        if self.current_style() != self.applied {
            out.push(SettingsEvent::Preview(self.applied.clone()));
        }
        out.push(SettingsEvent::Closed);
        out
    }

    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> Vec<SettingsEvent> {
        let mut events = Vec::new();
        let mut closed = false;
        let size = vec2(460.0, 822.0);
        crate::downloads::window(ctx, "type_ii_settings", "Type II Graph Settings", size, vec2(417.0, 600.0), p, |ui, close| {
            if std::mem::take(&mut self.focus_due) {
                ui.ctx().send_viewport_cmd(egui::ViewportCommand::Focus);
            }
            events = self.frame(ui, p);
            closed = close;
        });
        if closed && !events.contains(&SettingsEvent::Closed) {
            events.extend(self.close_events());
        }
        events
    }
}

/// `_ColorButton`: its colour as the background, the name on it in dark or
/// light text by the colour's lightness, a grey 1 px border.
fn color_button(ui: &Ui, rect: Rect, id: Id, color: &str) -> bool {
    let c = color_or_white(color);
    let fill = Color32::from_rgb(c[0], c[1], c[2]);
    let (mx, mn) = (c[0].max(c[1]).max(c[2]) as u32, c[0].min(c[1]).min(c[2]) as u32);
    let lightness = (mx + mn) / 2;
    let fg = if lightness > 150 { Color32::from_rgb(0x10, 0x10, 0x10) } else { Color32::from_rgb(0xf5, 0xf5, 0xf5) };
    let resp = ui.interact(rect, id, egui::Sense::click());
    qt::qt_box(ui, rect, 10.0, fill, Color32::from_rgb(0x80, 0x80, 0x80));
    widgets::text_center(ui, rect.center(), color, qt::FONT_PX, Weight::Regular, fg);
    resp.clicked()
}
