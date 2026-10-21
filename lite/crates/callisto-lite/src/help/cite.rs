//! Cite this Software (`CitationDialog`): the recommended citation and the
//! BibTeX entry, each with a copy button.

use egui::{Id, Ui};

use crate::fonts::Weight;
use crate::theme::Palette;
use crate::ui::message_box::{Icon, MessageBox};
use crate::ui::qlayout::{BoxLayout, Item, Layout, Policy, Size, WidgetSpec};
use crate::ui::{qt, widgets};

pub const RECOMMENDED_CITATION_TEXT: &str = "If you use e-CALLISTO FITS Analyzer in your research, please cite this article.\n\n\
G. L. S. S. Liyanage, J. Adassuriya, K. P. S. C. Jayaratne, C. Monstein, and \
P. K. Manoharan. \"e-CALLISTO FITS analyzer: a software framework for CALLISTO solar \
radio data.\" RAS Techniques and Instruments, 5, rzag056, 2026.\n\
DOI: 10.1093/rasti/rzag056\n\
URL: https://doi.org/10.1093/rasti/rzag056";

pub const CITATION_BIBTEX: &str = "@article{liyanage2026ecallistofitsanalyzersoftware,
      title={e-CALLISTO FITS analyzer: a software framework for CALLISTO solar radio data},
      author={Liyanage, G L S S and Adassuriya, J and Jayaratne, K P S C and Monstein, C and Manoharan, P K},
      journal={RAS Techniques and Instruments},
      volume={5},
      pages={rzag056},
      year={2026},
      month={01},
      issn={2752-8200},
      doi={10.1093/rasti/rzag056},
      url={https://doi.org/10.1093/rasti/rzag056},
}";

const INTRO: &str = "If you use this software for research, please cite the paper below. You can copy either the formatted citation or the BibTeX entry.";

pub struct CitationDialog {
    citation: String,
    bibtex: String,
    copied: Option<MessageBox>,
    first_frame: bool,
}

impl Default for CitationDialog {
    fn default() -> Self {
        Self::new()
    }
}

impl CitationDialog {
    pub fn new() -> Self {
        CitationDialog {
            citation: RECOMMENDED_CITATION_TEXT.into(),
            bibtex: CITATION_BIBTEX.into(),
            copied: None,
            first_frame: true,
        }
    }

    fn layout(ui: &Ui, width: i32) -> Layout {
        let buttons = BoxLayout::h([0; 4], 10)
            .widget(qt::button_spec(ui, "copy_citation", "Copy Citation", false))
            .widget(qt::button_spec(ui, "copy_bibtex", "Copy BibTeX", false))
            .stretch(1)
            .widget(qt::button_spec(ui, "close", "Close", false));
        let intro_lines = qt::wrap_lines(ui.ctx(), INTRO, (width - 28) as f32, Weight::Regular).len() as i32;
        BoxLayout::v([14; 4], 10)
            .widget(WidgetSpec::new(
                "intro",
                Size::new(150, intro_lines * qt::LINE as i32),
                Policy::Preferred,
                Policy::Preferred,
            ))
            .widget(qt::label_spec(ui, "citation_label", "Citation"))
            .widget(qt::view_spec("citation"))
            .widget(qt::label_spec(ui, "bibtex_label", "BibTeX"))
            .add_stretched(Item::Widget(qt::view_spec("bibtex")), 1)
            .add(buttons.into_item())
            .into()
    }

    /// The contents; false once closed.
    pub fn frame(&mut self, ui: &mut Ui, p: &Palette) -> bool {
        qt::with_app_style(|| self.contents(ui, p))
    }

    fn contents(&mut self, ui: &mut Ui, p: &Palette) -> bool {
        let r = ui.max_rect();
        let (w, h) = (r.width().round() as i32, r.height().round() as i32);
        let g = Self::layout(ui, w).layout_window(Size::new(w, h));
        let rect = |id: &str| g.rect(id).to_egui(r.min);
        let intro = rect("intro");
        let lines = qt::wrap_lines(ui.ctx(), INTRO, intro.width(), Weight::Regular);
        for (k, l) in lines.iter().enumerate() {
            let cy = intro.top() + k as f32 * qt::LINE + qt::LINE / 2.0;
            widgets::text_left(ui, intro.left(), cy, l, qt::FONT_PX, Weight::Regular, p.text);
        }
        qt::label(ui, rect("citation_label"), "Citation", p.text);
        qt::label(ui, rect("bibtex_label"), "BibTeX", p.text);
        let citation_id = Id::new("cite_citation");
        if std::mem::take(&mut self.first_frame) {
            ui.memory_mut(|m| m.request_focus(citation_id));
        }
        qt::plain_text_edit(ui, rect("citation"), citation_id, &mut self.citation, "", true, p);
        qt::plain_text_edit(ui, rect("bibtex"), Id::new("cite_bibtex"), &mut self.bibtex, "", true, p);
        if qt::button(ui, rect("copy_citation"), Id::new("cite_copy_citation"), "Copy Citation", false, true, p)
            .clicked()
        {
            ui.ctx().copy_text(self.citation.clone());
            self.copied = Some(MessageBox::ok(Icon::Information, "Copied", "Citation text copied to clipboard."));
        }
        if qt::button(ui, rect("copy_bibtex"), Id::new("cite_copy_bibtex"), "Copy BibTeX", false, true, p).clicked() {
            ui.ctx().copy_text(self.bibtex.clone());
            self.copied = Some(MessageBox::ok(Icon::Information, "Copied", "BibTeX entry copied to clipboard."));
        }
        let close = qt::button(ui, rect("close"), Id::new("cite_close"), "Close", false, true, p).clicked();
        let escape = self.copied.is_none() && ui.input(|i| i.key_pressed(egui::Key::Escape));
        !(close || escape)
    }

    /// The dialog in its own window; false once closed.
    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> bool {
        let builder = egui::ViewportBuilder::default()
            .with_title("Cite this Software")
            .with_inner_size(egui::vec2(760.0, 560.0))
            .with_min_inner_size(egui::vec2(299.0, 400.0));
        let bg = p.page_bg;
        let mut open = true;
        ctx.show_viewport_immediate(egui::ViewportId::from_hash_of("cite_software"), builder, |ctx, _| {
            if ctx.input(|i| i.viewport().close_requested()) {
                open = false;
            }
            egui::CentralPanel::default().frame(egui::Frame::NONE.fill(bg)).show(ctx, |ui| {
                if !self.frame(ui, p) {
                    open = false;
                }
            });
        });
        if let Some(b) = &mut self.copied
            && b.show(ctx, "cite_copied", p).is_some()
        {
            self.copied = None;
        }
        open
    }
}
