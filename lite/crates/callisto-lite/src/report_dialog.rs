//! "Generate Project Report": the progress dialog
//! (`_show_project_report_progress_dialog`, a `QProgressDialog` with a
//! Cancel button) while `ProjectReportWorker` writes the report.

use std::path::PathBuf;

use callisto_core::report::project::{Cancelled, ReportInput, ReportResult, generate_project_report_pdf};
use egui::{Id, Rect, pos2, vec2};

use crate::downloads::jobs::Job;
use crate::theme::Palette;
use crate::ui::qt;

/// How the worker ended (`finished`, `failed`, `cancelled`).
pub enum Outcome {
    Finished(ReportResult),
    Failed(String),
    Cancelled,
}

/// What the dialog hands the main window each frame.
pub enum Event {
    /// A progress text for the status bar (`showMessage(text, 2500)`).
    Progress(String),
    /// Cancel was pressed.
    Cancel,
    Done(Outcome),
}

pub struct ReportDialog {
    label: String,
    progress: qt::Progress,
    job: Job<(i32, String), Outcome>,
}

impl ReportDialog {
    /// Starts the worker on `input`, writing `path`.
    pub fn start(ctx: &egui::Context, path: PathBuf, input: ReportInput) -> Self {
        let job = Job::spawn(ctx, move |r| {
            if r.cancelled() {
                return Outcome::Cancelled;
            }
            let mut progress = |v: i32, t: &str| {
                if r.cancelled() {
                    return false;
                }
                r.send((v, t.to_owned()));
                true
            };
            match generate_project_report_pdf(&path, &input, &mut progress) {
                Ok(_) if r.cancelled() => Outcome::Cancelled,
                Ok(result) => Outcome::Finished(result),
                Err(e) if e.is::<Cancelled>() => Outcome::Cancelled,
                Err(e) => Outcome::Failed(e.to_string()),
            }
        });
        ReportDialog { label: "Generating project report...".into(), progress: qt::Progress::default(), job }
    }

    /// Draws the dialog; returns what happened this frame.
    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> Vec<Event> {
        let mut events = Vec::new();
        let (msgs, done) = self.job.poll();
        for (v, text) in msgs {
            self.progress.value = v.clamp(0, 100) as i64;
            if !text.is_empty() {
                self.label = text.clone();
                events.push(Event::Progress(text));
            }
        }
        if let Some(outcome) = done {
            events.push(Event::Done(outcome));
            return events;
        }
        let width = {
            let w = ctx.fonts_mut(|f| f.layout_no_wrap(self.label.clone(), crate::fonts::ui_font(12.0, crate::fonts::Weight::Regular), egui::Color32::WHITE).size().x);
            (w.ceil() + 22.0).max(300.0)
        };
        let size = vec2(width, 104.0);
        let builder = egui::ViewportBuilder::default()
            .with_title("Generate Project Report")
            .with_inner_size(size)
            .with_resizable(false)
            .with_minimize_button(false)
            .with_maximize_button(false)
            .with_always_on_top();
        let (label, progress, bg) = (self.label.clone(), self.progress.clone(), p.page_bg);
        let mut cancel = false;
        ctx.show_viewport_immediate(egui::ViewportId::from_hash_of("project_report_progress"), builder, |ctx, _| {
            if ctx.input(|i| i.viewport().close_requested()) {
                // Closing the dialog cancels, as its Cancel button does.
                ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
                cancel = true;
            }
            egui::CentralPanel::default().frame(egui::Frame::NONE.fill(bg)).show(ctx, |ui| {
                let full = ui.max_rect();
                let top = Rect::from_min_max(full.min, pos2(full.max.x, full.top() + 70.0));
                ui.scope_builder(egui::UiBuilder::new().max_rect(top), |ui| crate::downloads::import::body(ui, &label, &progress, p));
                let b = Rect::from_min_size(pos2(full.right() - 11.0 - 80.0, full.bottom() - 11.0 - 25.0), vec2(80.0, 25.0));
                if qt::button(ui, b, Id::new("report_cancel"), "Cancel", false, !self.job.cancel_requested(), p).clicked() {
                    cancel = true;
                }
            });
        });
        if cancel && !self.job.cancel_requested() {
            self.job.request_cancel();
            events.push(Event::Cancel);
        }
        ctx.request_repaint_after(std::time::Duration::from_millis(100));
        events
    }
}
