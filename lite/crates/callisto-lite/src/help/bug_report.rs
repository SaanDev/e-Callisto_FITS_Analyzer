//! Report a Bug (`BugReportDialog`): a title, what happened, the
//! diagnostics bundle, and buttons to write the bundle, copy the issue
//! text or open a prefilled GitHub issue.

use std::path::PathBuf;

use callisto_core::bug_report::{self, BugContext};
use callisto_core::pyjson::Json;
use egui::{Id, Ui};

use crate::fonts::Weight;
use crate::theme::Palette;
use crate::ui::message_box::{Icon, MessageBox};
use crate::ui::qlayout::{BoxLayout, Item, Layout, Policy, Size, WidgetSpec};
use crate::ui::{qt, widgets};

const DETAILS_LABEL: &str = "What happened / Steps to reproduce / Expected behavior";
const TITLE_PLACEHOLDER: &str = "Short summary (e.g., Crash when exporting PNG)";
const DETAILS_PLACEHOLDER: &str = "1. What you did\n2. What happened\n3. What you expected\n4. Any extra notes";

/// What the dialog needs from the main window when a button wants it:
/// the session's context and its provenance report (JSON and Markdown).
pub type Provider<'a> = &'a dyn Fn() -> (BugContext, Json, String);

pub struct BugReportDialog {
    repo: String,
    pub title: String,
    pub details: String,
    bundle: Option<PathBuf>,
    box_: Option<MessageBox>,
    first_frame: bool,
}

impl BugReportDialog {
    pub fn new(repo: &str) -> Self {
        BugReportDialog {
            repo: repo.to_owned(),
            title: String::new(),
            details: String::new(),
            bundle: None,
            box_: None,
            first_frame: true,
        }
    }

    /// Sets the bundle the label shows (as a capture does).
    pub fn set_bundle(&mut self, path: Option<PathBuf>) {
        self.bundle = path;
    }

    fn diag_text(&self) -> String {
        match &self.bundle {
            Some(p) => format!("Diagnostics bundle: {}", p.display()),
            None => "Diagnostics bundle: not generated yet.".into(),
        }
    }

    fn issue_body(&self, ctx: &BugContext) -> String {
        let bundle = self.bundle.as_ref().map(|p| p.display().to_string());
        bug_report::issue_body(&self.details, ctx, bundle.as_deref())
    }

    fn layout(ui: &Ui, diag_lines: i32) -> Layout {
        let title_row = BoxLayout::h([0; 4], 10)
            .widget(qt::label_spec(ui, "title_label", "Bug Title"))
            .add_stretched(Item::Widget(qt::line_edit_spec("title")), 1);
        let buttons = BoxLayout::h([0; 4], 10)
            .widget(qt::button_spec(ui, "generate", "Generate Diagnostics...", false))
            .widget(qt::button_spec(ui, "copy", "Copy Issue Text", false))
            .widget(qt::button_spec(ui, "open", "Open GitHub Issue", false))
            .stretch(1)
            .widget(qt::button_spec(ui, "close", "Close", false));
        BoxLayout::v([14; 4], 10)
            .add(title_row.into_item())
            .widget(qt::label_spec(ui, "details_label", DETAILS_LABEL))
            .add_stretched(Item::Widget(qt::view_spec("details")), 1)
            .widget(WidgetSpec::new(
                "diag",
                Size::new(104, diag_lines * qt::LINE as i32),
                Policy::Preferred,
                Policy::Preferred,
            ))
            .add(buttons.into_item())
            .into()
    }

    /// The contents; false once closed.
    pub fn frame(&mut self, ui: &mut Ui, p: &Palette, provider: Provider) -> bool {
        qt::with_app_style(|| self.contents(ui, p, provider))
    }

    fn contents(&mut self, ui: &mut Ui, p: &Palette, provider: Provider) -> bool {
        let r = ui.max_rect();
        let (w, h) = (r.width().round() as i32, r.height().round() as i32);
        let diag = self.diag_text();
        let diag_lines = qt::wrap_lines(ui.ctx(), &diag, (w - 28) as f32, Weight::Regular);
        let g = Self::layout(ui, diag_lines.len() as i32).layout_window(Size::new(w, h));
        let rect = |id: &str| g.rect(id).to_egui(r.min);
        qt::label(ui, rect("title_label"), "Bug Title", p.text);
        let title_id = Id::new("bug_title");
        if std::mem::take(&mut self.first_frame) {
            ui.memory_mut(|m| m.request_focus(title_id));
        }
        qt::line_edit(ui, rect("title"), title_id, &mut self.title, TITLE_PLACEHOLDER, true, p);
        qt::label(ui, rect("details_label"), DETAILS_LABEL, p.text);
        qt::plain_text_edit(
            ui,
            rect("details"),
            Id::new("bug_details"),
            &mut self.details,
            DETAILS_PLACEHOLDER,
            false,
            p,
        );
        let d = rect("diag");
        for (k, l) in diag_lines.iter().enumerate() {
            widgets::text_left(
                ui,
                d.left(),
                d.top() + k as f32 * qt::LINE + qt::LINE / 2.0,
                l,
                qt::FONT_PX,
                Weight::Regular,
                p.text,
            );
        }
        if qt::button(ui, rect("generate"), Id::new("bug_generate"), "Generate Diagnostics...", false, true, p)
            .clicked()
        {
            self.generate(provider);
        }
        if qt::button(ui, rect("copy"), Id::new("bug_copy"), "Copy Issue Text", false, true, p).clicked() {
            let (ctx, _, _) = provider();
            ui.ctx().copy_text(self.issue_body(&ctx));
            self.box_ = Some(MessageBox::ok(Icon::Information, "Copied", "Issue text copied to clipboard."));
        }
        if qt::button(ui, rect("open"), Id::new("bug_open"), "Open GitHub Issue", false, true, p).clicked() {
            let (ctx, _, _) = provider();
            let title = if self.title.trim().is_empty() { "Bug Report" } else { self.title.trim() };
            match bug_report::issue_url(&self.repo, title, &self.issue_body(&ctx)) {
                Ok(url) => {
                    if let Err(e) = open::that_detached(&url) {
                        self.box_ = Some(MessageBox::ok(
                            Icon::Warning,
                            "Open URL Failed",
                            &format!("Could not open GitHub issue page automatically.\n\nURL:\n{url}\n\n{e}"),
                        ));
                    }
                }
                Err(e) => {
                    self.box_ = Some(MessageBox::ok(
                        Icon::Critical,
                        "Report a Bug",
                        &format!("Could not build issue URL:\n{e}"),
                    ))
                }
            }
        }
        let close = qt::button(ui, rect("close"), Id::new("bug_close"), "Close", false, true, p).clicked();
        let escape = self.box_.is_none() && ui.input(|i| i.key_pressed(egui::Key::Escape));
        !(close || escape)
    }

    fn generate(&mut self, provider: Provider) {
        let start = super::default_download_dir();
        let Some(path) = rfd::FileDialog::new()
            .set_title("Save Diagnostics Bundle")
            .set_directory(&start)
            .set_file_name(bug_report::default_filename())
            .add_filter("Zip Archive (*.zip)", &["zip"])
            .save_file()
        else {
            return;
        };
        let (ctx, prov, prov_md) = provider();
        let notes = self.issue_body(&ctx);
        match bug_report::write_bundle(&path, &ctx, Some((&prov, &prov_md)), &notes) {
            Ok(out) => {
                self.box_ = Some(MessageBox::ok(
                    Icon::Information,
                    "Diagnostics Ready",
                    &format!("Diagnostics bundle created:\n{}", out.display()),
                ));
                self.bundle = Some(out);
            }
            Err(e) => {
                self.box_ = Some(MessageBox::ok(
                    Icon::Critical,
                    "Diagnostics Failed",
                    &format!("Could not generate diagnostics bundle:\n{e}"),
                ));
            }
        }
    }

    /// The dialog in its own window; false once closed.
    pub fn show(&mut self, ctx: &egui::Context, p: &Palette, provider: Provider) -> bool {
        let builder = egui::ViewportBuilder::default()
            .with_title("Report a Bug")
            .with_inner_size(egui::vec2(760.0, 460.0))
            .with_min_inner_size(egui::vec2(502.0, 380.0));
        let bg = p.page_bg;
        let mut open = true;
        ctx.show_viewport_immediate(egui::ViewportId::from_hash_of("report_bug"), builder, |ctx, _| {
            if ctx.input(|i| i.viewport().close_requested()) {
                open = false;
            }
            egui::CentralPanel::default().frame(egui::Frame::NONE.fill(bg)).show(ctx, |ui| {
                if !self.frame(ui, p, provider) {
                    open = false;
                }
            });
        });
        if let Some(b) = &mut self.box_
            && b.show(ctx, "bug_report_box", p).is_some()
        {
            self.box_ = None;
        }
        open
    }
}
