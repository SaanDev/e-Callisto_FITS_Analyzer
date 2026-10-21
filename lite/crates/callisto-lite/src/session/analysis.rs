//! The analysis session the Maximum Intensities window, the Analyzer and the
//! Type II band-splitting window share (`MainWindow._analysis_session` and
//! its helpers): its seed from the plot on show, the source context, the
//! spectrum ridge tracking follows, and the sidebar's Analysis Summary.

use std::sync::Arc;

use callisto_core::analysis::maxima::analysis_seed;
use callisto_core::analysis::session::{AnalysisSession, AnalyzerBlock, MaxIntensityBlock, SessionSource, TypeIIBlock, UiBlock};
use callisto_core::radio::array::Array2;

use super::{Session, normalize_plot_type};

impl Session {
    /// `_analysis_source_context`.
    pub fn analysis_source_context(&self) -> SessionSource {
        let Some(d) = &self.data else { return SessionSource::default() };
        SessionSource {
            filename: d.filename.clone(),
            is_combined: d.combined_mode.is_some(),
            combined_mode: d.combined_mode.map(|m| m.key().to_owned()),
            combined_sources: d.combined_sources.clone(),
            shape: Some([d.raw.shape().0, d.raw.shape().1]),
        }
    }

    /// `_is_isolated_burst_plot`.
    pub fn is_isolated_burst_plot(&self) -> bool {
        let t = normalize_plot_type(&self.current_plot_type).trim().to_lowercase();
        t == "isolated burst" || t == "isolated"
    }

    /// `_current_dynamic_spectrum_source_data`: what the plot was made from,
    /// else the display data back in the source's units, else the
    /// noise-reduced or raw data.
    pub fn current_dynamic_spectrum_source(&self) -> Option<Arc<Array2>> {
        let usable = |a: &Arc<Array2>| a.shape().1 > 0;
        if let Some(s) = self.current_plot_source.as_ref().filter(|a| usable(a)) {
            return Some(s.clone());
        }
        if let Some(display) = self.current_display_data.as_ref().filter(|a| usable(a)) {
            if self.display_converts_to_db() {
                let (cold, _) = self.db_hot_cold_digits();
                let scale = self.intensity_db_scale();
                let (rows, cols) = display.shape();
                let mut out = Array2::filled(rows, cols, 0.0);
                for r in 0..rows {
                    for (o, v) in out.row_mut(r).iter_mut().zip(display.row(r)) {
                        *o = (*v as f64 / scale + cold) as f32;
                    }
                }
                return Some(Arc::new(out));
            }
            return Some(display.clone());
        }
        if let Some(n) = self.noise_reduced.as_ref().filter(|a| usable(a)) {
            return Some(n.clone());
        }
        self.data.as_ref().map(|d| d.raw.clone()).filter(usable)
    }

    /// `_build_analysis_seed_from_current_data`.
    pub fn build_analysis_seed(&self, auto_clean_isolated: bool) -> Option<AnalysisSession> {
        let d = self.data.as_ref()?;
        let data = self.current_dynamic_spectrum_source()?;
        let clean = self.is_isolated_burst_plot() && auto_clean_isolated;
        let seed = analysis_seed(&data, &d.freqs, Some(&d.time), clean)?;
        let session = AnalysisSession {
            identity: Default::default(),
            source: self.analysis_source_context(),
            max_intensity: Some(MaxIntensityBlock {
                time_channels: seed.time_channels,
                time_seconds: Some(seed.time_seconds),
                freqs: seed.freqs,
                fundamental: true,
                harmonic: false,
            }),
            analyzer: AnalyzerBlock::default(),
            type_ii: TypeIIBlock::default(),
            ui: UiBlock {
                restore_max_window: true,
                restore_analyzer_window: false,
                restore_type_ii_window: false,
                auto_outlier_cleaned: seed.auto_outlier_cleaned,
                auto_removed_count: seed.auto_removed,
            },
        };
        session.is_meaningful().then_some(session)
    }

    /// What `_open_or_focus_type_ii_dialog` hands the Type II window: the
    /// noise-reduced spectrum in display units, its colormap, frequency step
    /// and noise clipping levels. None without noise-reduced data.
    pub fn type_ii_spectrum(&self) -> Option<crate::analysis::type_ii_window::Spectrum> {
        let d = self.data.as_ref()?;
        let noise = self.noise_reduced.as_ref()?;
        Some(crate::analysis::type_ii_window::Spectrum {
            display: self.intensity_for_display(noise),
            freqs: d.freqs.to_vec(),
            time: d.time.to_vec(),
            unit: self.intensity_unit_label().to_owned(),
            cmap: callisto_core::plot::Colormap::by_name(&self.graph.cmap),
            frequency_step_mhz: d.frequency_step_mhz,
            display_levels: self.threshold_display_levels(),
        })
    }

    /// The candidate `_open_or_focus_type_ii_dialog` makes without a session.
    pub fn empty_type_ii_session(&self) -> AnalysisSession {
        AnalysisSession {
            identity: Default::default(),
            source: self.analysis_source_context(),
            max_intensity: None,
            analyzer: AnalyzerBlock::default(),
            type_ii: TypeIIBlock::default(),
            ui: UiBlock { restore_type_ii_window: true, ..UiBlock::default() },
        }
    }

    /// `_analysis_session_with_context`: a session with this data's source.
    pub fn with_analysis_context(&self, session: Option<&AnalysisSession>) -> Option<AnalysisSession> {
        let mut s = session?.clone();
        if !s.is_meaningful() {
            return None;
        }
        s.source = self.analysis_source_context();
        s.identity = s.identity.ensured();
        Some(s)
    }

    /// `_merge_seed_with_existing_analysis`.
    pub fn merge_seed_with_existing(&self, seed: Option<AnalysisSession>, existing: Option<&AnalysisSession>) -> Option<AnalysisSession> {
        let existing = self.with_analysis_context(existing);
        match seed {
            None => existing,
            Some(seed) => Some(AnalysisSession::merged_with(seed, existing.as_ref())),
        }
    }

    /// `_refresh_analysis_summary_panel`.
    pub fn analysis_summary_text(&self) -> String {
        match self.analysis.as_ref().filter(|s| s.is_meaningful()) {
            Some(s) => s.summary_text(),
            None => "No analysis session loaded.".into(),
        }
    }

    /// `plot_max_intensities`: the main plot's lasso stops first.
    pub fn stop_main_lasso(&mut self) {
        if self.tools.lasso_active {
            self.tools.lasso_active = false;
            self.canvas.stop_interaction_capture();
        }
    }

    /// `_log_operation`, for the analysis windows.
    pub fn log_analysis(&mut self, message: impl Into<String>) {
        self.log_operation(message);
    }

    /// `_clear_analysis_session_state(close_windows=True)`: the windows
    /// close on the next frame.
    pub(super) fn clear_analysis_session(&mut self) {
        self.analysis = None;
        self.analysis_windows_close_due = true;
    }
}
