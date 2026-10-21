//! The comparison's Export... options (`ComparisonExportDialog`): the
//! visible view as shown, or a grid of every panel with its columns, title
//! and resolution.

use egui::{Id, Rect, Ui, pos2, vec2};

use crate::downloads::{at, lay_out};
use crate::fonts::Weight;
use crate::theme::Palette;
use crate::ui::qlayout::{BoxLayout, Container, FormLayout, Item, Layout, Policy, Size, WidgetSpec};
use crate::ui::{qt, widgets};

pub const DEFAULT_TITLE: &str = "e-CALLISTO Multi-Station Comparison";
const NOTE: &str = "Grid exports use a shared time axis, each station's native frequency range, and a white publication-style background.";
const COLUMNS: [Option<usize>; 4] = [None, Some(2), Some(3), Some(4)];

/// `ComparisonExportOptions`.
#[derive(Clone, Debug, PartialEq)]
pub struct ExportOptions {
    /// `EXPORT_LAYOUT_GRID`; otherwise the visible view.
    pub grid: bool,
    pub columns: Option<usize>,
    pub title: String,
    pub dpi: i32,
}

impl Default for ExportOptions {
    fn default() -> Self {
        Self { grid: false, columns: None, title: DEFAULT_TITLE.into(), dpi: 300 }
    }
}

pub struct ExportDialog {
    grid: bool,
    columns: usize,
    title: String,
    dpi: i32,
    /// The window's height: the size hint, grown to the minimum when the note shows.
    height: Option<f32>,
}

impl ExportDialog {
    pub fn new(o: &ExportOptions) -> Self {
        let columns = COLUMNS.iter().position(|c| *c == o.columns).unwrap_or(0);
        let title = if o.title.is_empty() { DEFAULT_TITLE.to_owned() } else { o.title.clone() };
        Self { grid: o.grid, columns, title, dpi: o.dpi.clamp(72, 1200), height: None }
    }

    /// `selected_options`.
    pub fn selected(&self) -> ExportOptions {
        let title = self.title.trim();
        ExportOptions {
            grid: self.grid,
            columns: COLUMNS[self.columns],
            title: if title.is_empty() { DEFAULT_TITLE.into() } else { title.to_owned() },
            dpi: self.dpi,
        }
    }

    /// For captures and tests.
    pub fn set_grid(&mut self, grid: bool) {
        self.grid = grid;
    }

    fn layout(&self, ui: &Ui) -> Layout {
        let radio = |id: &'static str, text: &str| {
            WidgetSpec::new(
                id,
                Size::new(widgets::text_width(ui, text, qt::FONT_PX, Weight::Regular).ceil() as i32 + 24, 17),
                Policy::Minimum,
                Policy::Fixed,
            )
        };
        let group = |id: &'static str, l: Layout| {
            Item::Container(Container {
                spec: WidgetSpec::new(id, Size::new(0, 0), Policy::Preferred, Policy::Preferred),
                margins: [13, 27, 13, 13],
                layout: Box::new(l),
            })
        };
        let layout_box = BoxLayout::v([9; 4], 6)
            .widget(radio("visible", "Visible View"))
            .widget(radio("grid", "Grid / Table Layout"));
        let form = FormLayout::new([9; 4], 6, 6)
            .row(
                qt::label_spec(ui, "columns_label", "Columns"),
                Item::Widget(qt::combo_spec(ui, "columns", &["Auto", "2", "3", "4"])),
            )
            .row(qt::label_spec(ui, "title_label", "Overall title"), Item::Widget(qt::line_edit_spec("title")))
            .row(
                qt::label_spec(ui, "dpi_label", "Raster resolution"),
                Item::Widget(qt::spin_spec(ui, "dpi", "1200 DPI")),
            );
        let buttons = BoxLayout::h([0; 4], 6)
            .stretch(1)
            .widget(qt::button_spec(ui, "ok", "OK", false))
            .widget(qt::button_spec(ui, "cancel", "Cancel", false));
        BoxLayout::v([11; 4], 6)
            .add(group("layout_group", layout_box.into()))
            .add(group("grid_group", form.into()))
            .widget(qt::wrap_label_spec(ui, "note", NOTE, false).hidden(!self.grid))
            .add(Item::Container(Container {
                spec: WidgetSpec::new("button_box", Size::new(113, 46), Policy::Expanding, Policy::Fixed),
                margins: [0; 4],
                layout: Box::new(buttons.into()),
            }))
            .into()
    }

    /// The height Qt gives the window: the size hint at first, then at least
    /// the layout's minimum for its width once the note shows.
    fn wanted_height(&self, ui: &Ui) -> f32 {
        let s = self.layout(ui).summary();
        let note = if self.grid {
            let lines = qt::wrap_lines(ui.ctx(), NOTE, 418.0, Weight::Regular).len().max(1) as i32;
            (lines - 1) * qt::LINE as i32
        } else {
            0
        };
        match self.height {
            None => s.hint.h as f32,
            Some(h) => h.max((s.min.h + note) as f32),
        }
    }

    /// `Some(Some(options))` on OK, `Some(None)` on Cancel or close.
    pub fn frame(&mut self, ui: &mut Ui, p: &Palette) -> Option<Option<ExportOptions>> {
        qt::with_app_style(|| self.contents(ui, p))
    }

    fn contents(&mut self, ui: &mut Ui, p: &Palette) -> Option<Option<ExportOptions>> {
        let mut out = None;
        let layout = self.layout(ui);
        let (g, o) = lay_out(&layout, ui.max_rect());
        let r = |id: &str| at(&g, o, id);
        qt::group_box(ui, r("layout_group"), "Layout", p);
        qt::group_box(ui, r("grid_group"), "Grid / Table Options", p);
        if crate::analysis::radio(ui, r("visible"), Id::new("cmp_export_visible"), "Visible View", !self.grid, true, p)
        {
            self.grid = false;
        }
        if crate::analysis::radio(ui, r("grid"), Id::new("cmp_export_grid"), "Grid / Table Layout", self.grid, true, p)
        {
            self.grid = true;
        }
        // `grid_group.setEnabled(grid)`: the stylesheet keeps label and field text colours.
        let on = self.grid;
        for (id, text) in
            [("columns_label", "Columns"), ("title_label", "Overall title"), ("dpi_label", "Raster resolution")]
        {
            let lr = r(id);
            // QFormLayout gives the label the row's height less the field's
            // padding; the text sits centred in the label's own box.
            let label = Rect::from_min_size(lr.min, vec2(lr.width(), lr.height().min(28.0)));
            qt::label(ui, label, text, qt::text_color(on, p));
        }
        let items: Vec<String> = ["Auto", "2", "3", "4"].iter().map(|s| (*s).to_owned()).collect();
        if let Some(i) =
            qt::combo(ui, r("columns"), Id::new("cmp_export_columns"), &items, Some(self.columns), "", on, p)
        {
            self.columns = i;
        }
        line_edit_at_end(ui, r("title"), Id::new("cmp_export_title"), &mut self.title, on, p);
        qt::spin_box_step(ui, r("dpi"), Id::new("cmp_export_dpi"), &mut self.dpi, (72, 1200), 50, " DPI", "", on, p);
        if self.grid {
            qt::wrapped(ui, r("note"), NOTE, p.text, false);
        }
        if qt::button(ui, r("ok"), Id::new("cmp_export_ok"), "OK", false, true, p).clicked() {
            out = Some(Some(self.selected()));
        }
        if qt::button(ui, r("cancel"), Id::new("cmp_export_cancel"), "Cancel", false, true, p).clicked() {
            out = Some(None);
        }
        if ui.input(|i| i.key_pressed(egui::Key::Escape)) {
            out = Some(None);
        }
        out
    }

    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> Option<Option<ExportOptions>> {
        let size = vec2(440.0, self.height.unwrap_or(377.0));
        crate::ui::dialogs::dialog_window(ctx, "comparison_export", "Export Comparison", size, p, None, |ui| {
            let want = self.wanted_height(ui);
            if self.height != Some(want) {
                self.height = Some(want);
                ui.ctx().send_viewport_cmd(egui::ViewportCommand::InnerSize(vec2(440.0, want)));
            }
            self.frame(ui, p)
        })
    }

    /// The window size the dialog wants (tests and captures).
    pub fn window_size(&mut self, ui: &Ui) -> egui::Vec2 {
        let want = self.wanted_height(ui);
        self.height = Some(want);
        vec2(440.0, want)
    }
}

/// `QLineEdit` after `setText`: the cursor at the end, so text wider than
/// the field shows its end until the field is edited.
fn line_edit_at_end(ui: &mut Ui, rect: Rect, id: Id, text: &mut String, enabled: bool, p: &Palette) {
    let focused = ui.memory(|m| m.has_focus(id));
    // `QLineEdit` text area: the 13 px inset, Qt's 2 px margins, the cursor.
    let visible = rect.width() - 13.0 - 11.0 - 2.0 - 2.0;
    let width = widgets::text_width(ui, text, qt::FONT_PX, Weight::Regular);
    if focused || width <= visible {
        qt::line_edit(ui, rect, id, text, "", enabled, p);
        return;
    }
    let mut shown = text.clone();
    // The field itself (focus, editing) with its own text hidden.
    qt::with_hidden_text(|| qt::line_edit(ui, rect, id, &mut shown, "", enabled, p));
    let left = rect.left() + 13.0 + 2.0;
    let clip = Rect::from_min_max(pos2(left, rect.top() + 1.0), pos2(left + visible + 1.0, rect.bottom() - 1.0));
    let x = left + visible - width;
    widgets::text_left_clipped(
        ui,
        clip,
        x,
        rect.center().y,
        text,
        qt::FONT_PX,
        Weight::Regular,
        qt::text_color(enabled, p),
    );
}
