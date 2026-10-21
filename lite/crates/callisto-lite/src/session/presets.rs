//! Processing → Presets (`MainWindow._preset_settings_payload`,
//! `_apply_preset_payload`, `_apply_preset_background`): the window's
//! processing settings as a preset, and a preset applied back.

use callisto_core::presets::PRESET_SCHEMA_VERSION;
use callisto_core::pyjson::{Json, Object};

use super::project::{rfi_from, rfi_json, style_defaults_from, style_defaults_json};
use super::*;

impl Session {
    /// `_preset_settings_payload`.
    pub fn preset_settings_payload(&self) -> Json {
        let clamp = |v: f64| self.clamp_noise_threshold(v).round_ties_even() as i64;
        let g = &self.graph;
        let mut graph = Object::new();
        for (k, v) in [
            ("remove_titles", g.remove_titles),
            ("title_bold", g.title_bold),
            ("title_italic", g.title_italic),
            ("axis_bold", g.axis_bold),
            ("axis_italic", g.axis_italic),
            ("ticks_bold", g.ticks_bold),
            ("ticks_italic", g.ticks_italic),
        ] {
            graph.insert(k.into(), Json::Bool(v));
        }
        graph.insert("title_override".into(), g.title_override.clone().into());
        graph.insert("font_family".into(), g.font_family.clone().into());
        graph.insert("tick_font_px".into(), Json::Int(g.tick_px as i64));
        graph.insert("axis_label_font_px".into(), Json::Int(g.axis_px as i64));
        graph.insert("title_font_px".into(), Json::Int(g.title_px as i64));
        let mut o = Object::new();
        o.insert("lower_slider".into(), Json::Int(clamp(self.noise_clip_low)));
        o.insert("upper_slider".into(), Json::Int(clamp(self.noise_clip_high)));
        o.insert("noise_clip_low".into(), Json::Float(self.noise_clip_low));
        o.insert("noise_clip_high".into(), Json::Float(self.noise_clip_high));
        o.insert(
            "noise_clip_scale".into(),
            Json::Str(if self.noise_clip_scale == ClipScale::SignedLog { "signed_log" } else { "linear" }.into()),
        );
        o.insert("background_method".into(), Json::Str(self.background_applied.unwrap_or(self.background_method).key().into()));
        o.insert("background_subtracted".into(), Json::Bool(self.background_applied.is_some()));
        o.insert("use_db".into(), Json::Bool(self.use_db));
        o.insert("use_utc".into(), Json::Bool(self.use_utc));
        o.insert("cmap".into(), Json::Str(if self.graph.cmap.is_empty() { "Custom".into() } else { self.graph.cmap.clone() }));
        o.insert("graph".into(), Json::Object(graph));
        o.insert("rfi".into(), rfi_json(&self.rfi));
        o.insert("annotation_style_defaults".into(), style_defaults_json(&self.tools.annotation_style));
        Json::Object(o)
    }

    /// The preset just saved or applied (`_active_preset_snapshot`).
    pub fn set_active_preset(&mut self, preset: Json) {
        self.project_extras.active_preset = Some(preset);
    }

    /// `_apply_preset_payload`; the full app's message when its version is unknown.
    pub fn apply_preset_payload(&mut self, preset: &Json, replot: bool, mark_dirty: bool, log: bool) -> Result<(), String> {
        let version = preset.get("version").map_or(Some(PRESET_SCHEMA_VERSION), Json::as_i64).unwrap_or(PRESET_SCHEMA_VERSION);
        if version != PRESET_SCHEMA_VERSION {
            return Err(format!("Preset version {version} is not supported by this build."));
        }
        let settings = match preset.get("settings") {
            Some(s @ Json::Object(_)) => s.clone(),
            _ => Json::object(),
        };
        self.project_extras.active_preset = Some(preset.clone());
        self.apply_preset_background(&settings, replot);

        // `_noise_thresholds_from_mapping`.
        let low = settings.get("noise_clip_low").or(settings.get("lower_slider")).and_then(Json::as_f64).unwrap_or(self.noise_clip_low);
        let high = settings.get("noise_clip_high").or(settings.get("upper_slider")).and_then(Json::as_f64).unwrap_or(self.noise_clip_high);
        let scale = match settings.get("noise_clip_scale") {
            Some(v) if v.py_str().trim().to_lowercase() == "signed_log" => ClipScale::SignedLog,
            Some(_) => ClipScale::Linear,
            None => self.noise_clip_scale,
        };
        self.expand_noise_clip_bounds_for_values(&[low, high]);
        let (mut lo, mut hi) = (self.clamp_noise_threshold(low), self.clamp_noise_threshold(high));
        if lo > hi {
            std::mem::swap(&mut lo, &mut hi);
        }
        self.set_noise_clip_state(Some(lo), Some(hi), Some(scale));
        self.use_db = settings.get("use_db").is_some_and(Json::truthy);
        self.use_utc = settings.get("use_utc").is_some_and(Json::truthy);
        self.graph.cmap = settings.get("cmap").filter(|v| v.truthy()).map(Json::py_str).unwrap_or_else(|| "Custom".into());
        if let Some(r) = settings.get("rfi").filter(|r| r.truthy()) {
            self.rfi = rfi_from(r);
        }
        let style = settings.get("annotation_style_defaults").filter(|s| s.truthy()).cloned();
        self.tools.annotation_style =
            style_defaults_from(Some(&style.unwrap_or_else(|| style_defaults_json(&self.tools.annotation_style))));

        let graph = settings.get("graph").cloned().unwrap_or_else(Json::object);
        let g = &mut self.graph;
        let flag = |k: &str, current: bool| graph.get(k).map_or(current, Json::truthy);
        g.remove_titles = flag("remove_titles", g.remove_titles);
        g.title_bold = flag("title_bold", g.title_bold);
        g.title_italic = flag("title_italic", g.title_italic);
        g.axis_bold = flag("axis_bold", g.axis_bold);
        g.axis_italic = flag("axis_italic", g.axis_italic);
        g.ticks_bold = flag("ticks_bold", g.ticks_bold);
        g.ticks_italic = flag("ticks_italic", g.ticks_italic);
        if let Some(t) = graph.get("title_override") {
            g.title_override = t.py_str();
        }
        g.font_family = crate::fonts::normalize_font_family(&graph.get("font_family").map(Json::py_str).unwrap_or_default());
        let size = |k: &str, current: u32| graph.get(k).and_then(Json::as_i64).map_or(current, |v| v.clamp(1, 200) as u32);
        let sizes = (size("tick_font_px", g.tick_px), size("axis_label_font_px", g.axis_px), size("title_font_px", g.title_px));
        if replot && sizes != (g.tick_px, g.axis_px, g.title_px) {
            // A spin box set to another value ends the hardware default sizes
            // (the preset applied on load sets them with signals blocked).
            g.hw_default_sizes = false;
        }
        (g.tick_px, g.axis_px, g.title_px) = sizes;

        if self.has_data() {
            let product = self.current_product();
            self.fit_noise_clip_bounds_to_data(product.as_deref());
        }
        if replot && self.has_data() {
            let title = self.current_plot_type.clone();
            self.plot_data(&title, PlotView::Keep);
        }
        if mark_dirty {
            self.mark_project_dirty();
        }
        if log {
            let name = preset.get("name").map(Json::py_str).unwrap_or_else(|| "Unnamed".into());
            self.log_operation(format!("Applied preset: {name}"));
        }
        Ok(())
    }

    /// `_apply_preset_background`: the processed data in line with the
    /// preset. Presets from before the Background Subtraction section record
    /// only thresholds; any non-zero one meant a subtracted view.
    fn apply_preset_background(&mut self, settings: &Json, push_undo: bool) -> bool {
        let method = settings
            .get("background_method")
            .map_or(Some(self.background_method), |m| BackgroundMethod::normalize(&m.py_str(), true).ok())
            .unwrap_or_default();
        self.background_method = method;
        let wanted = match settings.get("background_subtracted") {
            Some(b) => b.truthy(),
            None => ["noise_clip_low", "noise_clip_high"]
                .iter()
                .any(|k| settings.get(k).and_then(Json::as_f64).unwrap_or(0.0).abs() > 1e-9),
        };
        if !self.has_data() {
            return false;
        }
        let applied = self.background_applied;
        if wanted && applied == Some(method) && self.noise_reduced.is_some() {
            return false;
        }
        if !wanted && (applied.is_none() || self.noise_reduced.is_none()) {
            return false;
        }
        if wanted {
            let Some(data) = self.background_subtracted_from_raw(method) else { return false };
            if push_undo {
                self.push_undo_state();
            }
            self.set_background_product(method, Some(data));
        } else {
            if push_undo {
                self.push_undo_state();
            }
            self.noise_reduced = None;
            self.noise_reduced_original = None;
            self.noise_reduced_original_plot_type = "Background Subtracted".into();
            (self.noise_vmin, self.noise_vmax) = (None, None);
            self.background_applied = None;
            self.current_plot_type = "Raw".into();
        }
        self.tools.lasso_mask = None;
        self.rfi_preview = None;
        self.rfi.applied = false;
        true
    }

    /// `export_view_config`'s file: the range on show and the visual settings.
    pub fn view_config_text(&self) -> Option<String> {
        let [t0, t1, f0, f1] = self.display_range_defaults()?;
        let range = DisplayRange::normalized(t0, t1, f0, f1).ok()?;
        let cfg = callisto_core::view_config::ViewConfig::build(Some(range), self.batch_env().visual, self.use_utc);
        Some(cfg.dump())
    }

    /// `_apply_view_config_payload`: the visual settings, a full replot, then
    /// the range.
    pub fn apply_view_config(&mut self, cfg: &callisto_core::view_config::ViewConfig) -> bool {
        let v = &cfg.visual;
        let scale = if v.noise_clip_scale == "signed_log" { ClipScale::SignedLog } else { ClipScale::Linear };
        self.expand_noise_clip_bounds_for_values(&[v.noise_clip_low, v.noise_clip_high]);
        let (mut lo, mut hi) = (self.clamp_noise_threshold(v.noise_clip_low), self.clamp_noise_threshold(v.noise_clip_high));
        if lo > hi {
            std::mem::swap(&mut lo, &mut hi);
        }
        self.set_noise_clip_state(Some(lo), Some(hi), Some(scale));
        self.use_db = v.use_db;
        self.use_utc = v.use_utc;
        self.graph.cmap = if v.cmap.is_empty() { "Custom".into() } else { v.cmap.clone() };
        let graph = callisto_core::pyjson::from_serde(&serde_json::Value::Object(v.graph.clone()));
        let g = &mut self.graph;
        let flag = |k: &str, current: bool| graph.get(k).map_or(current, Json::truthy);
        g.remove_titles = flag("remove_titles", g.remove_titles);
        g.title_bold = flag("title_bold", g.title_bold);
        g.title_italic = flag("title_italic", g.title_italic);
        g.axis_bold = flag("axis_bold", g.axis_bold);
        g.axis_italic = flag("axis_italic", g.axis_italic);
        g.ticks_bold = flag("ticks_bold", g.ticks_bold);
        g.ticks_italic = flag("ticks_italic", g.ticks_italic);
        if let Some(t) = graph.get("title_override") {
            g.title_override = t.py_str();
        }
        g.font_family = crate::fonts::normalize_font_family(&graph.get("font_family").map(Json::py_str).unwrap_or_default());
        let size = |k: &str, current: u32| graph.get(k).and_then(Json::as_i64).map_or(current, |x| x.clamp(1, 200) as u32);
        (g.tick_px, g.axis_px, g.title_px) = (size("tick_font_px", g.tick_px), size("axis_label_font_px", g.axis_px), size("title_font_px", g.title_px));
        self.mark_project_dirty();
        if self.has_data() {
            let title = self.current_plot_type.clone();
            self.plot_data(&title, PlotView::Full);
        }
        match cfg.range {
            Some(r) => self.apply_display_range(r.time_start_s, r.time_stop_s, r.freq_min_mhz, r.freq_max_mhz, true),
            None => true,
        }
    }
}
