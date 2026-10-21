//! "Importing FITS Files": the main window's progress dialog
//! (`_show_import_progress_dialog`, a `QProgressDialog` without a cancel
//! button) while `DownloaderImportWorker` or `DownloaderComparisonWorker`
//! prepares a selection.

use std::path::PathBuf;

use callisto_core::radio::downloader::{self, ImportPayload, ImportProgress};
use egui::{Rect, Ui, pos2, vec2};

use super::jobs::Job;
use crate::theme::Palette;
use crate::ui::qt;

enum Run {
    Import(Job<ImportProgress, Result<ImportPayload, String>>),
    Compare(Job<ImportProgress, Result<Vec<String>, String>>),
}

pub enum Outcome {
    Import(Result<ImportPayload, String>),
    Compare(Result<Vec<String>, String>),
}

pub struct ImportDialog {
    label: String,
    progress: qt::Progress,
    width: f32,
    run: Run,
}

impl ImportDialog {
    pub fn import(ctx: &egui::Context, cache_root: PathBuf, sources: Vec<String>) -> Self {
        let label = downloader::import_label(&sources).to_owned();
        let n = sources.len() as i64;
        let job = Job::spawn(ctx, move |r| downloader::prepare_import(&cache_root, &sources, |p| r.send(p)));
        Self::with(ctx, label, n, Run::Import(job))
    }

    pub fn compare(ctx: &egui::Context, cache_root: PathBuf, sources: Vec<String>) -> Self {
        let label = downloader::comparison_label(&sources).to_owned();
        let n = sources.len() as i64;
        let job = Job::spawn(ctx, move |r| downloader::prepare_comparison(&cache_root, &sources, |p| r.send(p)));
        Self::with(ctx, label, n, Run::Compare(job))
    }

    fn with(ctx: &egui::Context, label: String, n: i64, run: Run) -> Self {
        let mut d = ImportDialog { label, progress: qt::Progress::range(n.max(1)), width: 200.0, run };
        d.fit(ctx);
        d
    }

    /// `ensureSizeIsAtLeastSizeHint`: wide enough for the label, never narrower.
    fn fit(&mut self, ctx: &egui::Context) {
        let w = ctx.fonts_mut(|f| {
            f.layout_no_wrap(
                self.label.clone(),
                crate::fonts::ui_font(12.0, crate::fonts::Weight::Regular),
                egui::Color32::WHITE,
            )
            .size()
            .x
        });
        self.width = self.width.max((w.ceil() + 22.0).max(200.0));
    }

    fn apply(&mut self, ctx: &egui::Context, msgs: Vec<ImportProgress>) {
        for m in msgs {
            match m {
                ImportProgress::Text(t) => {
                    self.label = if t.is_empty() { "Importing FITS files...".into() } else { t };
                    self.fit(ctx);
                }
                ImportProgress::Range(a, b) => {
                    self.progress.min = a;
                    self.progress.max = b.max(a);
                    self.progress.value = self.progress.value.max(a);
                }
                ImportProgress::Value(v) => {
                    self.progress.max = self.progress.max.max(v);
                    self.progress.value = v;
                }
            }
        }
    }

    /// Draws the dialog; returns the outcome once the work is done.
    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> Option<Outcome> {
        let outcome = match &mut self.run {
            Run::Import(job) => {
                let (msgs, done) = job.poll();
                self.apply(ctx, msgs);
                done.map(Outcome::Import)
            }
            Run::Compare(job) => {
                let (msgs, done) = job.poll();
                self.apply(ctx, msgs);
                done.map(Outcome::Compare)
            }
        };
        if outcome.is_some() {
            return outcome;
        }
        let size = vec2(self.width, 70.0);
        let builder = egui::ViewportBuilder::default()
            .with_title("Importing FITS Files")
            .with_inner_size(size)
            .with_resizable(false)
            .with_minimize_button(false)
            .with_maximize_button(false)
            .with_always_on_top();
        let (label, progress, bg) = (self.label.clone(), self.progress.clone(), p.page_bg);
        ctx.show_viewport_immediate(egui::ViewportId::from_hash_of("import_progress"), builder, |ctx, _| {
            if ctx.input(|i| i.viewport().close_requested()) {
                // No cancel button: the import goes on.
                ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
            }
            ctx.send_viewport_cmd(egui::ViewportCommand::InnerSize(size));
            egui::CentralPanel::default()
                .frame(egui::Frame::NONE.fill(bg))
                .show(ctx, |ui| body(ui, &label, &progress, p));
        });
        ctx.request_repaint_after(std::time::Duration::from_millis(100));
        None
    }
}

/// The dialog's contents in `ui` (`QProgressDialogPrivate::layout`: 11 px
/// sides, the centred label above the bar).
pub fn body(ui: &Ui, label: &str, progress: &qt::Progress, p: &Palette) {
    let full = ui.max_rect();
    let lh = full.height() - 11.0 - 26.0 - 6.0;
    let label_rect = Rect::from_min_size(pos2(full.left() + 11.0, full.top()), vec2(full.width() - 22.0, lh));
    crate::ui::widgets::text_center(ui, label_rect.center(), label, qt::FONT_PX, crate::fonts::Weight::Regular, p.text);
    let bar = Rect::from_min_size(pos2(full.left() + 11.0, full.top() + lh + 6.0), vec2(full.width() - 22.0, 26.0));
    qt::progress_bar(ui, bar, progress, p);
}
