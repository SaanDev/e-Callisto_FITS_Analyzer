//! Processing → RFI Cleaning: preview, apply and reset (`MainWindow`'s
//! `_preview_rfi_from_dialog`, `apply_rfi_now`, `reset_rfi`).

use callisto_core::radio::processing::{RfiSettings, clean_rfi};

use super::*;

/// `_rfi_config`.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct RfiConfig {
    pub settings: RfiSettings,
    pub masked_channel_indices: Vec<usize>,
    pub applied: bool,
}

impl Session {
    /// `_rfi_source_data`: the processed product, or the raw data.
    fn rfi_source_data(&self) -> Option<Arc<Array2>> {
        self.current_product()
    }

    /// `open_rfi_panel`: whether the panel may open.
    pub fn can_open_rfi_panel(&mut self) -> bool {
        if !self.has_data() {
            self.warn(NoticeKind::Information, "RFI Cleaning", "Load a FITS file first.");
            return false;
        }
        true
    }

    /// `_preview_rfi_from_dialog`.
    pub fn preview_rfi(&mut self, settings: RfiSettings) {
        self.rfi.settings = settings;
        let Some(src) = self.rfi_source_data() else {
            self.warn(NoticeKind::Information, "RFI Cleaning", "Load a FITS file first.");
            return;
        };
        let result = clean_rfi(&src, settings);
        self.rfi_preview = Some(Arc::new(result.data));
        self.rfi = RfiConfig { settings, masked_channel_indices: result.masked_channel_indices, applied: false };
        let preview = self.rfi_preview.clone().unwrap();
        let title = self.current_plot_type.clone();
        self.plot_source(preview, &title, PlotView::Keep);
        self.show_message("RFI preview updated.", 3000);
    }

    /// `_apply_rfi_from_dialog` / `apply_rfi_now`.
    pub fn apply_rfi(&mut self, settings: Option<RfiSettings>) {
        if let Some(s) = settings {
            self.rfi.settings = s;
        }
        let Some(src) = self.rfi_source_data() else {
            self.warn(NoticeKind::Information, "Apply RFI", "Load a FITS file first.");
            return;
        };
        let cfg = self.rfi.settings;
        if self.rfi_preview.is_none() {
            self.preview_rfi(cfg);
        }
        let Some(preview) = self.rfi_preview.clone() else { return };
        let backup_type = normalize_plot_type(&self.current_plot_type);
        self.push_undo_state();
        self.noise_reduced = Some(preview);
        self.noise_reduced_original = Some(src);
        self.noise_reduced_original_plot_type = backup_type;
        self.current_plot_type = "RFI Cleaned".into();
        self.rfi.applied = true;
        self.plot_data("RFI Cleaned", PlotView::Keep);
        self.log_operation(format!(
            "Applied RFI cleaning (kT={}, kF={}, z>{}, pct={}).",
            cfg.kernel_time,
            cfg.kernel_freq,
            crate::session::py_float(cfg.channel_z_threshold),
            crate::session::py_float(cfg.percentile_clip)
        ));
    }

    /// `reset_rfi`.
    pub fn reset_rfi(&mut self) {
        self.rfi_preview = None;
        self.rfi = RfiConfig::default();
        if self.has_data() {
            let title = self.current_plot_type.clone();
            self.plot_data(&title, PlotView::Keep);
        }
        self.show_message("RFI settings reset.", 2500);
    }

    /// The panel's "Masked channels" line (`set_masked_channels`).
    pub fn rfi_masked_text(&self) -> String {
        let indices = &self.rfi.masked_channel_indices;
        if indices.is_empty() {
            return "Masked channels: 0".into();
        }
        let mut shown = indices.iter().take(8).map(|i| i.to_string()).collect::<Vec<_>>().join(", ");
        if indices.len() > 8 {
            shown.push_str(", ...");
        }
        format!("Masked channels: {} ({shown})", indices.len())
    }

    /// RFI cleaning re-run over a new raw array, for `_reapply_processing_chain`.
    pub(super) fn reapply_rfi(&mut self, data: Arc<Array2>, title: &str) -> Option<String> {
        if !self.rfi.applied {
            return None;
        }
        let result = clean_rfi(&data, self.rfi.settings);
        self.noise_reduced_original = Some(data);
        self.noise_reduced_original_plot_type = title.to_owned();
        (self.noise_vmin, self.noise_vmax) = finite_data_limits(&result.data.data).unzip();
        self.noise_reduced = Some(Arc::new(result.data));
        self.rfi.masked_channel_indices = result.masked_channel_indices;
        Some("RFI Cleaned".into())
    }
}
