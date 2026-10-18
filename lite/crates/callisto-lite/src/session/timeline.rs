//! The Timeline section: stepping the dataset through the archive.
//!
//! `extend_timeline`, `trim_timeline`, the availability probe and the
//! in-place dataset swap from `MainWindow`, with `TimelineExtendWorker` and
//! `TimelineProbeWorker` (`src/ui/app/gui_workers.py`) as background threads.

use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, TryRecvError};

use callisto_core::radio::cache::enforce_cache_limit;
use callisto_core::radio::combine::{self, Combined};
use callisto_core::radio::fits_io::{self, extract_ut_start_sec};
use callisto_core::radio::naming::basename;
use callisto_core::radio::timeline::{
    Direction, TimelineState, describe_timeline, extended_paths, probe_segment, resolve_segment, trimmed_paths,
};

use super::*;

/// `callisto_cache.DEFAULT_MAX_CACHE_BYTES`.
pub const DEFAULT_MAX_CACHE_BYTES: u64 = 2 * 1024 * 1024 * 1024;

/// Messages from the extend worker.
pub(super) enum TimelineMsg {
    Progress(String),
    Finished { combined: Box<Combined>, direction: Direction, added: usize },
    Failed(String),
}

/// What `_capture_dataset_state` adds to a normal undo state.
#[derive(Clone, Debug)]
pub(super) struct DatasetExtras {
    combined_mode: Option<CombineType>,
    combined_sources: Vec<String>,
    combined_options: Option<CombineOptions>,
    header0: Option<Header>,
    headers: Vec<(String, Header)>,
    source_path: Option<String>,
    ut_start_sec: Option<f64>,
}

/// Per-direction probe result: `(available, reason)` for next and previous.
pub type ProbeResult = [(bool, String); 2];

impl Dataset {
    pub(super) fn from_combined(c: Combined) -> Self {
        Dataset {
            raw: Arc::new(c.data),
            freqs: Arc::new(c.freqs),
            time: Arc::new(c.time),
            filename: c.filename,
            header0: Some(c.header0),
            headers: Vec::new(),
            source_path: None,
            ut_start_sec: c.ut_start_sec,
            combined_mode: Some(c.combine_type),
            combined_sources: c.sources,
            combined_options: c.options,
            gap_rows: c.gap_row_mask,
            frequency_step_mhz: c.frequency_step_mhz,
        }
    }

    pub(super) fn from_file(path: &str) -> anyhow::Result<Self> {
        let res = fits_io::load_callisto_fits(Path::new(path))?;
        let ut = extract_ut_start_sec(&res.header0);
        Ok(Dataset {
            raw: Arc::new(res.data),
            freqs: Arc::new(res.freqs),
            time: Arc::new(res.time),
            filename: basename(path).to_owned(),
            header0: Some(res.header0),
            headers: res.headers,
            source_path: Some(path.to_owned()),
            ut_start_sec: ut,
            combined_mode: None,
            combined_sources: Vec::new(),
            combined_options: None,
            gap_rows: None,
            frequency_step_mhz: None,
        })
    }
}

impl Session {
    /// Where adjacent observations are downloaded to, and whether the
    /// network may be used (tests run offline).
    pub fn set_archive(&mut self, cache_root: Option<PathBuf>, online: bool) {
        self.cache_root = cache_root;
        self.online = online;
    }

    pub fn timeline_busy(&self) -> bool {
        self.timeline_job.is_some()
    }

    /// `_capture_dataset_state`.
    pub(super) fn capture_dataset_state(&self) -> Option<Snapshot> {
        let mut s = self.capture_state()?;
        let d = self.data.as_ref()?;
        s.extras = Some(Box::new(DatasetExtras {
            combined_mode: d.combined_mode,
            combined_sources: d.combined_sources.clone(),
            combined_options: d.combined_options,
            header0: d.header0.clone(),
            headers: d.headers.clone(),
            source_path: d.source_path.clone(),
            ut_start_sec: d.ut_start_sec,
        }));
        Some(s)
    }

    /// `_restore_dataset_state_extras`.
    pub(super) fn restore_dataset_extras(&mut self, x: DatasetExtras) {
        let Some(d) = self.data.as_mut() else { return };
        d.combined_mode = x.combined_mode;
        d.combined_sources = x.combined_sources;
        d.combined_options = x.combined_options;
        d.header0 = x.header0;
        d.headers = x.headers;
        d.source_path = x.source_path;
        d.ut_start_sec = x.ut_start_sec;
        self.refresh_instrument_profile();
        self.refresh_timeline_panel();
    }

    /// `_push_undo_entry`.
    fn push_undo_entry(&mut self, s: Snapshot) {
        self.undo_stack.push(Entry::State(Box::new(s)));
        self.redo_stack.clear();
        self.trim_history();
    }

    /// `_refresh_timeline_panel` (the state part), then the availability probe.
    pub fn refresh_timeline_panel(&mut self) {
        let paths = self.timeline_source_paths();
        self.timeline = if paths.is_empty() { None } else { describe_timeline(&paths).ok() };
        self.timeline_message = None;
        self.timeline_available = None;
        if let Some(state) = self.timeline.clone() {
            self.start_timeline_probe(state);
        } else {
            self.timeline_probe = None;
        }
    }

    /// `_start_timeline_probe`: ask the archive in the background whether
    /// stepping is possible, prefetching when the Edit menu says so.
    fn start_timeline_probe(&mut self, state: TimelineState) {
        if !self.online {
            self.timeline_probe = None;
            return;
        }
        let (tx, rx) = mpsc::channel();
        let prefetch = self.prefetch_adjacent;
        let cache_root = self.cache_root.clone();
        std::thread::spawn(move || {
            let mut payload: ProbeResult = [(true, String::new()), (true, String::new())];
            for (i, direction) in [Direction::Next, Direction::Previous].into_iter().enumerate() {
                let (available, reason) = probe_segment(&state, direction);
                if available
                    && prefetch
                    && let Some(root) = &cache_root
                {
                    // Prefetch is best-effort; the real fetch will report.
                    let _ = resolve_segment(&state, direction, root, |_, _| {});
                }
                payload[i] = (available, reason);
            }
            let _ = tx.send(payload);
        });
        self.timeline_probe = Some(rx);
    }

    /// `extend_timeline`: add the adjacent observation(s) and re-combine.
    pub fn extend_timeline(&mut self, direction: Direction, steps: usize) {
        let Some(state) = self.timeline.clone() else {
            self.warn(NoticeKind::Information, "Timeline", "This dataset has no CALLISTO source files to step from.");
            return;
        };
        if self.timeline_busy() {
            return;
        }
        let Some(cache_root) = self.cache_root.clone() else {
            self.warn(NoticeKind::Warning, "Timeline", "No download folder is available for archive files.");
            return;
        };
        let steps = steps.max(1);
        let options = self.data.as_ref().and_then(|d| d.combined_options).unwrap_or_default();
        self.timeline_undo = self.capture_dataset_state().map(Box::new);
        self.timeline_message = Some(format!("Fetching {steps} observation(s)..."));
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            let send = |m| {
                let _ = tx.send(m);
            };
            let mut state = state;
            let mut added = 0;
            for step in 0..steps {
                send(TimelineMsg::Progress(format!("Fetching observation {} of {steps}...", step + 1)));
                let paths = match resolve_segment(&state, direction, &cache_root, |_, _| {}) {
                    Ok(paths) => paths,
                    Err(e) if added == 0 => return send(TimelineMsg::Failed(e)),
                    Err(e) => {
                        // Partial success: keep what we already have.
                        send(TimelineMsg::Progress(format!("Stopped after {added}: {e}")));
                        break;
                    }
                };
                let merged = extended_paths(&state, &paths, direction);
                state = match describe_timeline(&merged) {
                    Ok(s) => s,
                    Err(e) => return send(TimelineMsg::Failed(format!("Could not extend the timeline:\n{e}"))),
                };
                added += 1;
            }
            if added == 0 {
                return send(TimelineMsg::Failed("No further observations are available.".into()));
            }
            send(TimelineMsg::Progress("Combining...".into()));
            let combined = match combine::combine_compatible(&state.paths(), options) {
                Ok(c) => c,
                Err(e) => {
                    return send(TimelineMsg::Failed(format!("The extended selection could not be combined:\n{e}")));
                }
            };
            enforce_cache_limit(&cache_root, DEFAULT_MAX_CACHE_BYTES);
            send(TimelineMsg::Finished { combined: Box::new(combined), direction, added });
        });
        self.timeline_job = Some(rx);
    }

    /// Handle what the timeline workers reported since the last frame.
    /// Returns whether anything changed.
    pub(super) fn poll_timeline(&mut self) -> bool {
        let mut changed = false;
        if let Some(rx) = &self.timeline_job {
            loop {
                match rx.try_recv() {
                    Ok(TimelineMsg::Progress(text)) => {
                        self.timeline_message = Some(text);
                        changed = true;
                    }
                    Ok(TimelineMsg::Finished { combined, direction, added }) => {
                        self.timeline_job = None;
                        if let Some(undo) = self.timeline_undo.take() {
                            self.push_undo_entry(*undo);
                        }
                        let align = if direction == Direction::Previous { Align::End } else { Align::Start };
                        self.apply_extended_dataset(Dataset::from_combined(*combined), align);
                        let dir = if direction == Direction::Previous { "previous" } else { "next" };
                        self.log_operation(format!("Extended the timeline {dir} by {added} observation(s)."));
                        return true;
                    }
                    Ok(TimelineMsg::Failed(message)) => {
                        self.timeline_job = None;
                        self.timeline_undo = None;
                        self.refresh_timeline_panel();
                        self.timeline_message = Some(message);
                        return true;
                    }
                    Err(TryRecvError::Empty) => break,
                    Err(TryRecvError::Disconnected) => {
                        self.timeline_job = None;
                        self.refresh_timeline_panel();
                        return true;
                    }
                }
            }
        }
        if let Some(rx) = &self.timeline_probe {
            match rx.try_recv() {
                Ok(payload) => {
                    self.timeline_probe = None;
                    if !self.timeline_busy() && self.has_data() {
                        self.timeline_available = Some(payload);
                        changed = true;
                    }
                }
                Err(TryRecvError::Disconnected) => self.timeline_probe = None,
                Err(TryRecvError::Empty) => {}
            }
        }
        changed
    }

    /// Whether a worker is running, so the window keeps drawing.
    pub fn timeline_pending(&self) -> bool {
        self.timeline_job.is_some() || self.timeline_probe.is_some()
    }

    /// `trim_timeline`: drop the first or last observation and re-combine.
    pub fn trim_timeline(&mut self, trim_start: bool) {
        let Some(state) = self.timeline.clone() else { return };
        if self.timeline_busy() {
            return;
        }
        let remaining = match trimmed_paths(&state, trim_start) {
            Ok((paths, _shift)) => paths,
            Err(e) => {
                self.warn(NoticeKind::Information, "Timeline", e);
                return;
            }
        };
        let undo = self.capture_dataset_state();
        let single = remaining.len() == 1
            && describe_timeline(&remaining).is_ok_and(|s| s.segments.len() == 1 && s.focus_codes.len() == 1);
        let options = self.data.as_ref().and_then(|d| d.combined_options).unwrap_or_default();
        let dataset = if single {
            Dataset::from_file(&remaining[0]).map(|mut d| {
                d.combined_sources = remaining.clone();
                d
            })
        } else {
            combine::combine_compatible(&remaining, options).map(Dataset::from_combined)
        };
        let dataset = match dataset {
            Ok(d) => d,
            Err(e) => {
                self.warn(NoticeKind::Critical, "Timeline", format!("Could not trim the dataset:\n{e}"));
                return;
            }
        };
        if let Some(u) = undo {
            self.push_undo_entry(u);
        }
        self.apply_extended_dataset(dataset, if trim_start { Align::End } else { Align::Start });
        self.log_operation(format!("Trimmed the {} of the timeline.", if trim_start { "start" } else { "end" }));
    }

    /// `_time_shift_for_new_axis`: how far the retained samples move.
    fn time_shift_for_new_axis(&self, new_time: &[f64], align: Align) -> f64 {
        let Some(old) = self.data.as_ref().map(|d| d.time.clone()) else { return 0.0 };
        if align != Align::End {
            return 0.0;
        }
        let overlap = old.len().min(new_time.len());
        if overlap == 0 {
            return 0.0;
        }
        new_time[new_time.len() - overlap] - old[old.len() - overlap]
    }

    /// `_apply_extended_dataset`: swap in the re-combined dataset, keeping
    /// the processing the user had and showing the whole new range.
    fn apply_extended_dataset(&mut self, d: Dataset, align: Align) {
        // Nothing in Lite is marked in time yet (annotations, ruler, drift
        // come later); the shift is worked out for when they are.
        let _shift = self.time_shift_for_new_axis(&d.time, align);
        self.data = Some(d);
        self.refresh_instrument_profile();
        self.current_display_data = None;
        let title = self.reapply_processing_chain();
        self.plot_data(&title, PlotView::Full);
        self.refresh_timeline_panel();
    }

    /// `_reapply_processing_chain`: rebuild the product over the new raw data.
    fn reapply_processing_chain(&mut self) -> String {
        let wanted = normalize_plot_type(&self.current_plot_type);
        self.noise_reduced = None;
        self.noise_reduced_original = None;
        self.noise_reduced_original_plot_type = "Background Subtracted".into();
        let method = self.background_applied.take();
        let title = match method {
            Some(m) if wanted != "Raw" && self.set_background_product(m, None) => {
                let product = self.noise_reduced.clone();
                self.fit_noise_clip_bounds_to_data(product.as_deref());
                "Background Subtracted".to_owned()
            }
            _ => {
                let raw = self.data.as_ref().map(|d| d.raw.clone());
                self.fit_noise_clip_bounds_to_data(raw.as_deref());
                "Raw".to_owned()
            }
        };
        let title = if wanted == "RFI Cleaned"
            && let Some(data) = self.current_product()
            && let Some(rfi_title) = self.reapply_rfi(data, &title)
        {
            rfi_title
        } else {
            title
        };
        self.current_plot_type = title.clone();
        title
    }
}

/// Which end of the old axis the retained samples line up with.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Align {
    Start,
    End,
}
