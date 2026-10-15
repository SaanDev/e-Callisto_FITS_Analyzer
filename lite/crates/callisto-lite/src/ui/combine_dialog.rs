//! File → Combine FITS Files... (`CombineFitsDialog` in
//! `src/ui/radio/dialogs/combine_dialogs.py`): pick files, see what kind of
//! combination they make, choose the frequency options, preview, import.

use callisto_core::plot::Colormap;
use callisto_core::radio::combine::{
    self, CombineOptions, CombineType, Combined, FrequencyRelation, GapFill, Inspection, Overlap,
};
use callisto_core::radio::frequency_axis::{finite_data_limits, pyqtgraph_extent};
use egui::{Id, Rect, Ui, pos2, vec2};

use super::widgets;
use crate::canvas::{ImageUpdate, PlotCanvas};
use crate::fonts::Weight;
use crate::session::{Notice, NoticeKind};
use crate::theme::Palette;

const GAP_FILLS: [(GapFill, &str); 4] = [
    (GapFill::Background, "Interpolated background"),
    (GapFill::Hatched, "Gray hatched gap"),
    (GapFill::Average, "Average edge background"),
    (GapFill::Zero, "Zero fill"),
];
const OVERLAPS: [(Overlap, &str); 4] = [
    (Overlap::Split, "Split at connection frequency"),
    (Overlap::Low, "Keep low band in overlap"),
    (Overlap::High, "Keep high band in overlap"),
    (Overlap::Reject, "Reject overlap"),
];

/// `_mode_label`.
fn mode_label(t: Option<CombineType>) -> &'static str {
    match t {
        Some(CombineType::Time) => "Time",
        Some(CombineType::Frequency) => "Frequency",
        Some(CombineType::TimeFrequency) => "Time + frequency",
        None => "Unknown",
    }
}

/// What the dialog asks the window to do.
pub enum CombineAction {
    Import(Box<Combined>),
    Notice(Notice),
    Close,
}

pub struct CombineFitsDialog {
    paths: Vec<String>,
    inspection: Option<Inspection>,
    combined: Option<Combined>,
    summary: String,
    placeholder: String,
    gap_fill: usize,
    overlap: usize,
    connection: f64,
    connection_range: (f64, f64),
    preview: PlotCanvas,
}

impl Default for CombineFitsDialog {
    fn default() -> Self {
        Self::new()
    }
}

impl CombineFitsDialog {
    pub fn new() -> Self {
        Self {
            paths: Vec::new(),
            inspection: None,
            combined: None,
            summary:
                "Select consecutive time segments, matching frequency bands, or a complete timestamp × focus-code grid."
                    .into(),
            placeholder: "Combined output will appear here.".into(),
            gap_fill: 0,
            overlap: 0,
            connection: 0.0,
            connection_range: (0.0, 10000.0),
            preview: PlotCanvas::new(),
        }
    }

    fn relation(&self) -> Option<&FrequencyRelation> {
        self.inspection.as_ref()?.frequency_relation.as_ref()
    }

    fn is_frequency(&self) -> bool {
        matches!(
            self.inspection.as_ref().and_then(|i| i.combine_type),
            Some(CombineType::Frequency | CombineType::TimeFrequency)
        )
    }

    /// `load_files`.
    fn load_files(&mut self) -> Option<CombineAction> {
        let files = rfd::FileDialog::new()
            .set_title("Select FITS Files to Combine")
            .add_filter("FITS files (*.fit *.fits *.fit.gz *.fits.gz)", &["fit", "fits", "gz", "FIT", "FITS"])
            .pick_files()?;
        if files.len() < 2 {
            return Some(warning("Invalid Selection", "Select at least two FITS files."));
        }
        self.paths = files.iter().map(|p| p.to_string_lossy().into_owned()).collect();
        self.combined = None;
        let inspection = combine::inspect_combination(&self.paths);
        if !inspection.valid {
            let error = inspection.error.clone().unwrap_or_else(|| "The selected files cannot be combined.".into());
            self.summary = error.clone();
            self.placeholder = "Select a valid FITS combination to continue.".into();
            self.inspection = None;
            return Some(warning("Invalid Combination", &error));
        }
        let focus =
            if inspection.focus_codes.is_empty() { "n/a".to_owned() } else { inspection.focus_codes.join(", ") };
        self.summary = format!(
            "Detected mode: {}\nStation: {} | Files: {} | Timestamps: {} | Focus codes: {focus}",
            mode_label(inspection.combine_type),
            inspection.station,
            self.paths.len(),
            inspection.timestamp_groups.len()
        );
        self.inspection = Some(inspection);
        if let Some(rel) = self.relation()
            && !rel.overlaps.is_empty()
        {
            let lo = rel.overlaps.iter().map(|o| o.low).fold(f64::INFINITY, f64::min);
            let hi = rel.overlaps.iter().map(|o| o.high).fold(f64::NEG_INFINITY, f64::max);
            self.connection_range = (lo.min(hi), lo.max(hi));
            self.connection = 0.5 * (lo + hi);
        }
        self.placeholder = "Selection is valid. Click Combine and Preview.".into();
        None
    }

    fn options(&self) -> CombineOptions {
        if !self.is_frequency() {
            return CombineOptions::default();
        }
        let has_overlap = self.relation().is_some_and(|r| r.has_overlap());
        let policy = OVERLAPS[self.overlap].0;
        CombineOptions {
            gap_fill: GAP_FILLS[self.gap_fill].0,
            overlap_policy: policy,
            overlap_connection_mhz: (has_overlap && policy == Overlap::Split).then_some(self.connection),
        }
    }

    /// `combine_files` and `_render_preview`.
    fn combine_files(&mut self) -> Option<CombineAction> {
        if !self.inspection.as_ref().is_some_and(|i| i.valid) {
            return Some(warning("Invalid Selection", "Select a valid FITS combination first."));
        }
        match combine::combine_compatible(&self.paths, self.options()) {
            Ok(c) => {
                let extent = pyqtgraph_extent(&c.freqs, &c.time, c.frequency_step_mhz)?;
                self.preview.update_image(ImageUpdate {
                    data: std::sync::Arc::new(c.data.clone()),
                    extent,
                    cmap: Colormap::combine_preview(),
                    gap_rows: c.gap_row_mask.as_deref(),
                    levels: finite_data_limits(&c.data.data),
                    title: format!("{} combined preview", mode_label(Some(c.combine_type))),
                    x_label: "Time [s]".into(),
                    y_label: "Frequency [MHz]".into(),
                    colorbar_label: String::new(),
                    view: None,
                    freqs: &c.freqs,
                    log_freq: false,
                });
                self.combined = Some(c);
                None
            }
            Err(e) => {
                self.combined = None;
                Some(CombineAction::Notice(Notice {
                    kind: NoticeKind::Critical,
                    title: "Combine Error".into(),
                    text: e.to_string(),
                }))
            }
        }
    }

    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> Option<CombineAction> {
        let options_h = if self.is_frequency() { 7.0 + 12.0 + 3.0 * 32.0 + 2.0 * 6.0 + 12.0 + 6.0 } else { 0.0 };
        let size = vec2(620.0, 9.0 * 2.0 + 46.0 + 6.0 + 40.0 + 6.0 + options_h + 2.0 * (46.0 + 6.0) + 340.0);
        super::dialogs::dialog_window(ctx, "combine_fits", "Combine FITS Files", size, p, CombineAction::Close, |ui| {
            self.body(ui, p)
        })
    }

    fn body(&mut self, ui: &mut Ui, p: &Palette) -> Option<CombineAction> {
        let full = ui.max_rect().shrink(9.0);
        let mut y = full.top();
        let row = |y: f32, h: f32| Rect::from_min_size(pos2(full.left(), y), vec2(full.width(), h));

        let mut action = None;
        if widgets::dialog_button(ui, row(y, 46.0), Id::new("cf_select"), "Select FITS Files", true, p).clicked() {
            action = self.load_files();
        }
        y += 46.0 + 6.0;
        let galley = ui.ctx().fonts_mut(|f| {
            f.layout(self.summary.clone(), crate::fonts::ui_font(12.0, Weight::Regular), p.text, full.width())
        });
        ui.painter().galley(pos2(full.left(), y), galley, p.text);
        y += 40.0 + 6.0;

        if self.is_frequency() {
            let h = 7.0 + 12.0 + 3.0 * 32.0 + 2.0 * 6.0 + 12.0;
            let inner = widgets::group_box(ui, row(y, h), "Frequency Combine Options", true, p);
            let label_w = 150.0;
            let field = |i: usize| {
                Rect::from_min_size(
                    pos2(inner.left() + label_w, inner.top() + i as f32 * 38.0),
                    vec2(inner.width() - label_w, 32.0),
                )
            };
            for (i, label) in ["Gap handling", "Overlap handling", "Connection frequency"].into_iter().enumerate() {
                widgets::text_left(ui, inner.left(), field(i).center().y, label, 12.0, Weight::Regular, p.text);
            }
            let gap_labels: Vec<&str> = GAP_FILLS.iter().map(|(_, l)| *l).collect();
            if let Some(i) = widgets::combo(ui, field(0), Id::new("cf_gap"), &gap_labels, self.gap_fill, true, p) {
                self.gap_fill = i;
            }
            let overlap_labels: Vec<&str> = OVERLAPS.iter().map(|(_, l)| *l).collect();
            if let Some(i) = widgets::combo(ui, field(1), Id::new("cf_overlap"), &overlap_labels, self.overlap, true, p)
            {
                self.overlap = i;
            }
            let enabled = self.relation().is_some_and(|r| r.has_overlap()) && self.overlap == 0;
            let range = self.connection_range;
            widgets::spin_field(
                ui,
                field(2),
                Id::new("cf_connection"),
                &mut self.connection,
                range,
                0.001,
                3,
                " MHz",
                enabled,
                p,
            );
            y += h + 6.0;
        }

        let can_combine = self.inspection.as_ref().is_some_and(|i| i.valid);
        if widgets::dialog_button(ui, row(y, 46.0), Id::new("cf_combine"), "Combine and Preview", can_combine, p)
            .clicked()
        {
            action = self.combine_files();
        }
        y += 46.0 + 6.0;
        let can_import = self.combined.is_some();
        if widgets::dialog_button(ui, row(y, 46.0), Id::new("cf_import"), "Import to Analyzer", can_import, p).clicked()
            && let Some(c) = self.combined.take()
        {
            return Some(CombineAction::Import(Box::new(c)));
        }
        y += 46.0 + 6.0;

        let preview = Rect::from_min_max(pos2(full.left(), y), full.max);
        if self.preview.has_image() {
            self.preview.set_dark(p.dark);
            self.preview.show(ui, preview);
        } else {
            widgets::text_center(ui, preview.center(), &self.placeholder, 12.0, Weight::Regular, p.text);
        }
        action
    }
}

fn warning(title: &str, text: &str) -> CombineAction {
    CombineAction::Notice(Notice { kind: NoticeKind::Warning, title: title.into(), text: text.into() })
}
