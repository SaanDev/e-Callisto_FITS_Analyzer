//! `PreviewWindow`: one or more Plotutil median-dB spectrograms of the
//! files a preview prepared, in a non-modal window that stays on top; the
//! tab bar is hidden for a single panel.

use std::sync::atomic::{AtomicU64, Ordering};

use callisto_core::mpl::figures::{QtColors, preview_figure};
use callisto_core::radio::downloader::PreviewPanel;
use egui::{Id, Rect, vec2};

use crate::figure_view::FigureView;
use crate::theme::Palette;
use crate::ui::qlayout::{BoxLayout, Layout, Policy, Size, WidgetSpec};
use crate::ui::qt;

static NEXT_ID: AtomicU64 = AtomicU64::new(1);

struct Panel {
    source: PreviewPanel,
    view: FigureView,
    colors: Option<QtColors>,
}

pub struct PreviewWindow {
    id: u64,
    title: String,
    panels: Vec<Panel>,
    current: usize,
    size: egui::Vec2,
}

fn colors(p: &Palette) -> QtColors {
    let c = |c: egui::Color32| [c.r(), c.g(), c.b(), 255];
    QtColors { window: c(p.qt.window), base: c(p.qt.base), text: c(p.qt.window_text), mid: c(p.qt.mid) }
}

impl PreviewWindow {
    pub fn new(ctx: &egui::Context, panels: Vec<PreviewPanel>) -> Self {
        let title = if panels.len() == 1 { panels[0].title.clone() } else { format!("{} panels", panels.len()) };
        PreviewWindow {
            id: NEXT_ID.fetch_add(1, Ordering::Relaxed),
            title: if title.is_empty() { "FITS Preview".into() } else { format!("FITS Preview: {title}") },
            panels: panels.into_iter().map(|source| Panel { source, view: FigureView::default(), colors: None }).collect(),
            current: 0,
            size: super::fit_to_screen(ctx, 900.0, 600.0),
        }
    }

    /// Draws the window; returns true once it was closed.
    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> bool {
        let mut closed = false;
        let viewport = egui::ViewportId::from_hash_of(("fits_preview", self.id));
        let builder = egui::ViewportBuilder::default()
            .with_title(&self.title)
            .with_inner_size(self.size)
            .with_min_inner_size(vec2(640.0, 480.0))
            .with_always_on_top();
        let bg = p.page_bg;
        ctx.show_viewport_immediate(viewport, builder, |ctx, _| {
            if ctx.input(|i| i.viewport().close_requested()) {
                closed = true;
            }
            egui::CentralPanel::default().frame(egui::Frame::NONE.fill(bg)).show(ctx, |ui| self.body(ui, p));
        });
        closed
    }

    /// The window's contents in `ui` (tests).
    pub fn frame(&mut self, ui: &mut egui::Ui, p: &Palette) {
        self.body(ui, p);
    }

    pub fn title(&self) -> &str {
        &self.title
    }

    fn body(&mut self, ui: &mut egui::Ui, p: &Palette) {
        let full = ui.max_rect();
        // QVBoxLayout (11 px margins): the tab widget.
        let tabs = Rect::from_min_max(full.min + vec2(11.0, 11.0), full.max - vec2(11.0, 11.0));
        let titles: Vec<String> = self.panels.iter().map(|x| if x.source.title.is_empty() { "Preview".into() } else { x.source.title.clone() }).collect();
        let (page, clicked) = qt::fusion_tab_widget(ui, tabs, Id::new(("preview_tabs", self.id)), &titles, self.current, p);
        if let Some(i) = clicked {
            self.current = i;
        }
        let colors = colors(p);
        let Some(panel) = self.panels.get_mut(self.current) else { return };
        let mut v = BoxLayout::v([0; 4], 4);
        if !panel.source.subtitle.is_empty() {
            v = v.widget(qt::wrap_label_spec(ui, "caption", &panel.source.subtitle, true));
        }
        v = v.widget(WidgetSpec::new("canvas", Size::new(640, 480), Policy::Preferred, Policy::Preferred).min_hint(Size::new(10, 10)));
        let layout: Layout = v.into();
        let (g, o) = super::lay_out(&layout, page);
        if !panel.source.subtitle.is_empty() {
            qt::status(ui, super::at(&g, o, "caption"), &panel.source.subtitle, p);
        }
        let canvas = super::at(&g, o, "canvas");
        if panel.colors != Some(colors) || !panel.view.has_figure() {
            let s = &panel.source;
            panel.view.set(preview_figure(&s.data, &s.freqs, &s.time, canvas.width() as f64, canvas.height() as f64, colors));
            panel.colors = Some(colors);
        }
        panel.view.show(ui, canvas);
    }
}
