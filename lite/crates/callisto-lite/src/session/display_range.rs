//! View → Set Display Range and its presets (`MainWindow._apply_display_range`
//! and friends, `src/backend/session/view_config.py`).

use serde::{Deserialize, Serialize};

use super::*;

pub use callisto_core::view_config::{DisplayRange, VIEW_CONFIG_SCHEMA_VERSION};

/// A saved display range (`build_display_range_preset`), stored as the full
/// app stores it.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DisplayRangePreset {
    pub name: String,
    pub version: i64,
    pub created_at: String,
    pub range: DisplayRange,
    pub time_mode: String,
}

/// `normalize_name`: whitespace runs collapsed.
pub fn normalize_name(name: &str) -> String {
    name.split_whitespace().collect::<Vec<_>>().join(" ")
}

impl DisplayRangePreset {
    pub fn new(name: &str, range: DisplayRange, ut: bool) -> Result<Self, String> {
        let name = normalize_name(name);
        if name.is_empty() {
            return Err("Display range preset name is required.".into());
        }
        let range =
            DisplayRange::normalized(range.time_start_s, range.time_stop_s, range.freq_min_mhz, range.freq_max_mhz)?;
        Ok(Self {
            name,
            version: VIEW_CONFIG_SCHEMA_VERSION,
            created_at: chrono::Local::now().format("%Y-%m-%dT%H:%M:%S%:z").to_string(),
            range,
            time_mode: if ut { "ut".into() } else { "seconds".into() },
        })
    }
}

/// `upsert_display_range_preset`: replace by name (case-insensitive), keep sorted.
pub fn upsert_preset(items: &mut Vec<DisplayRangePreset>, preset: DisplayRangePreset) -> bool {
    let key = preset.name.to_lowercase();
    let mut replaced = false;
    for item in items.iter_mut() {
        if item.name.to_lowercase() == key {
            *item = preset.clone();
            replaced = true;
        }
    }
    if !replaced {
        items.push(preset);
    }
    items.sort_by_key(|p| p.name.to_lowercase());
    replaced
}

/// `delete_display_range_preset`.
pub fn delete_preset(items: &mut Vec<DisplayRangePreset>, name: &str) -> bool {
    let key = normalize_name(name).to_lowercase();
    if key.is_empty() {
        return false;
    }
    let before = items.len();
    items.retain(|p| p.name.to_lowercase() != key);
    items.len() != before
}

/// What the Set Display Range dialog opens with (`open_display_range_dialog`).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct DisplayRangeRequest {
    pub full: View,
    pub initial: [f64; 4],
    pub ut_start_sec: Option<f64>,
    pub ut_mode: bool,
}

/// How well a candidate UT day pairing fits: distance outside the data,
/// deviation from the asked duration, distance from the data start.
type Score = (f64, f64, f64);

fn clamp(v: f64, lo: f64, hi: f64) -> f64 {
    v.max(lo).min(hi)
}

impl Session {
    /// `_full_display_view`: the matplotlib extent, sorted.
    fn full_display_view(&self) -> Option<View> {
        let d = self.data.as_ref()?;
        let [x0, x1, y0, y1] = matplotlib_extent(&d.freqs, &d.time, d.frequency_step_mhz)?;
        if ![x0, x1, y0, y1].iter().all(|v| v.is_finite()) {
            return None;
        }
        Some(View { xlim: sorted(x0, x1), ylim: sorted(y0, y1) })
    }

    /// `_display_range_defaults`: the current view, or the full one.
    pub(super) fn display_range_defaults(&self) -> Option<[f64; 4]> {
        let full = self.full_display_view()?;
        let view = self.canvas.view().unwrap_or(full);
        let mut v = [view.xlim.0, view.xlim.1, view.ylim.0, view.ylim.1];
        if !v.iter().all(|x| x.is_finite()) {
            v = [full.xlim.0, full.xlim.1, full.ylim.0, full.ylim.1];
        }
        let (x0, x1) = sorted(v[0], v[1]);
        let (y0, y1) = sorted(v[2], v[3]);
        Some([x0, x1, y0, y1])
    }

    /// `open_display_range_dialog` (the part before the dialog).
    pub fn display_range_request(&mut self) -> Option<DisplayRangeRequest> {
        let (Some(full), Some(initial)) = (self.full_display_view(), self.display_range_defaults()) else {
            self.warn(
                NoticeKind::Information,
                "Set Display Range",
                "Load a FITS file before setting the display range.",
            );
            return None;
        };
        let ut_start_sec = self.data.as_ref().and_then(|d| d.ut_start_sec);
        Some(DisplayRangeRequest { full, initial, ut_start_sec, ut_mode: self.use_utc && ut_start_sec.is_some() })
    }

    /// `_ut_seconds_of_day_range_to_relative`: UT clock times to seconds from
    /// the file start, choosing the days that best fit the data.
    pub fn ut_range_to_relative(&self, start_sod: f64, stop_sod: f64, full_xlim: (f64, f64)) -> Option<(f64, f64)> {
        let ut_start = self.data.as_ref()?.ut_start_sec?;
        if ![start_sod, stop_sod, ut_start].iter().all(|v| v.is_finite()) || (stop_sod - start_sod).abs() <= 1e-9 {
            return None;
        }
        let (lo, hi) = sorted(full_xlim.0, full_xlim.1);
        if !(lo.is_finite() && hi.is_finite()) || hi <= lo {
            return None;
        }
        let nominal = if stop_sod > start_sod { stop_sod - start_sod } else { stop_sod + 86400.0 - start_sod };
        let day_span = 3.max(((hi - lo) / 86400.0).ceil() as i64 + 3);
        let mut best: Option<(Score, (f64, f64))> = None;
        for start_day in -1..day_span + 2 {
            let start_rel = start_sod - ut_start + start_day as f64 * 86400.0;
            for stop_day in -1..day_span + 3 {
                let stop_rel = stop_sod - ut_start + stop_day as f64 * 86400.0;
                if stop_rel <= start_rel {
                    continue;
                }
                let (cs, ce) = (clamp(start_rel, lo, hi), clamp(stop_rel, lo, hi));
                if ce <= cs {
                    continue;
                }
                let score = (
                    (start_rel - cs).abs() + (stop_rel - ce).abs(),
                    ((stop_rel - start_rel) - nominal).abs(),
                    (start_rel - lo).abs(),
                );
                if best.is_none_or(|(b, _)| score < b) {
                    best = Some((score, (start_rel, stop_rel)));
                }
            }
        }
        best.map(|(_, r)| r)
    }

    fn display_range_warning(&mut self, message: &str, show_errors: bool) {
        if show_errors {
            self.warn(NoticeKind::Warning, "Set Display Range", message);
        } else {
            self.show_message(message, 3000);
        }
    }

    /// `_apply_display_range`: clamp to the data and zoom there (undoable).
    pub fn apply_display_range(&mut self, t0: f64, t1: f64, f0: f64, f1: f64, show_errors: bool) -> bool {
        let Some(full) = self.full_display_view() else {
            self.display_range_warning("Load a FITS file before setting the display range.", show_errors);
            return false;
        };
        if ![t0, t1, f0, f1].iter().all(|v| v.is_finite()) {
            self.display_range_warning("Enter finite time and frequency limits.", show_errors);
            return false;
        }
        if t1 <= t0 {
            self.display_range_warning("Stop time must be later than start time.", show_errors);
            return false;
        }
        if (f1 - f0).abs() <= 1e-9 {
            self.display_range_warning("Frequency start and stop must be different.", show_errors);
            return false;
        }
        let x0 = clamp(t0, full.xlim.0, full.xlim.1);
        let x1 = clamp(t1, full.xlim.0, full.xlim.1);
        if x1 <= x0 {
            self.display_range_warning("Selected time range does not overlap the loaded data.", show_errors);
            return false;
        }
        let (flo, fhi) = sorted(f0, f1);
        let y0 = clamp(flo, full.ylim.0, full.ylim.1);
        let y1 = clamp(fhi, full.ylim.0, full.ylim.1);
        if y1 <= y0 {
            self.display_range_warning("Selected frequency range does not overlap the loaded data.", show_errors);
            return false;
        }
        let new_view = View { xlim: (x0, x1), ylim: (y0, y1) };
        if let Some(prev) = self.canvas.view()
            && !prev.close_to(&new_view, 1e-6)
        {
            self.push_undo_view(prev);
        }
        self.canvas.set_view(new_view);
        self.show_message("Display range applied.", 2500);
        true
    }

    /// What batch processing borrows from the main window: the colormap,
    /// the cold digits and a view config's range and visual settings
    /// (`_current_display_range_payload`, `_view_config_visual_payload`).
    pub fn batch_env(&self) -> crate::tools::batch::BatchEnv {
        use serde_json::{Value, json};
        let g = &self.graph;
        let graph = json!({
            "remove_titles": g.remove_titles,
            "title_bold": g.title_bold,
            "title_italic": g.title_italic,
            "axis_bold": g.axis_bold,
            "axis_italic": g.axis_italic,
            "ticks_bold": g.ticks_bold,
            "ticks_italic": g.ticks_italic,
            "title_override": g.title_override,
            "font_family": g.font_family,
            "tick_font_px": g.tick_px,
            "axis_label_font_px": g.axis_px,
            "title_font_px": g.title_px,
        });
        let (cold, _) = sorted(self.noise_clip_low, self.noise_clip_high);
        let visual = callisto_core::view_config::VisualConfig {
            use_db: self.use_db,
            use_utc: self.use_utc,
            noise_clip_low: self.noise_clip_low,
            noise_clip_high: self.noise_clip_high,
            noise_clip_scale: if self.noise_clip_scale == super::ClipScale::SignedLog { "signed_log" } else { "linear" }.into(),
            cmap: if g.cmap.is_empty() { "Custom".into() } else { g.cmap.clone() },
            graph: match graph {
                Value::Object(m) => m,
                _ => Default::default(),
            },
        };
        crate::tools::batch::BatchEnv {
            cmap: if g.cmap.trim().is_empty() { "Custom".into() } else { g.cmap.clone() },
            cold_digits: cold,
            display_range: self.display_range_defaults().and_then(|[t0, t1, f0, f1]| DisplayRange::normalized(t0, t1, f0, f1).ok()),
            visual,
            use_utc: self.use_utc,
        }
    }

    /// `_current_display_range_payload`.
    pub fn current_display_range(&mut self) -> Option<DisplayRange> {
        let Some([t0, t1, f0, f1]) = self.display_range_defaults() else {
            self.warn(
                NoticeKind::Information,
                "Save Display Range Preset",
                "Load a FITS file before saving a display range preset.",
            );
            return None;
        };
        DisplayRange::normalized(t0, t1, f0, f1).ok()
    }

    /// `_apply_display_range_payload` for a preset; reports like the full app.
    pub fn apply_display_range_preset(&mut self, preset: &DisplayRangePreset) {
        let r = preset.range;
        match DisplayRange::normalized(r.time_start_s, r.time_stop_s, r.freq_min_mhz, r.freq_max_mhz) {
            Ok(r) => {
                if self.apply_display_range(r.time_start_s, r.time_stop_s, r.freq_min_mhz, r.freq_max_mhz, true) {
                    self.show_message(format!("Applied display range preset '{}'.", preset.name), 3000);
                }
            }
            Err(e) => self.display_range_warning(&e, true),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn presets_upsert_and_delete_by_name() {
        let r = DisplayRange::normalized(0.0, 10.0, 80.0, 20.0).unwrap();
        assert_eq!((r.freq_min_mhz, r.freq_max_mhz), (20.0, 80.0));
        let mut items = Vec::new();
        assert!(!upsert_preset(&mut items, DisplayRangePreset::new("  Burst  one ", r, false).unwrap()));
        assert!(upsert_preset(&mut items, DisplayRangePreset::new("burst ONE", r, true).unwrap()));
        assert_eq!(items.len(), 1);
        assert_eq!(items[0].time_mode, "ut");
        assert!(delete_preset(&mut items, "Burst one"));
        assert!(items.is_empty());
        assert!(DisplayRange::normalized(5.0, 5.0, 1.0, 2.0).is_err());
    }
}
