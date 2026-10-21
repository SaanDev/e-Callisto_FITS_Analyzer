//! About → Check for Updates... and the startup check (`check_for_app_updates`,
//! `_on_update_check_finished`, `_start_update_download` and the download
//! progress dialog): Lite's releases on GitHub (`lite-` tags only), the
//! status bar's update label, the boxes and the installer download.

use std::path::PathBuf;
use std::time::{Duration, Instant};

use callisto_core::updates::{self, Download, UpdateCheck};
use egui::{Id, Rect, pos2, vec2};

use crate::downloads::jobs::Job;
use crate::fonts::Weight;
use crate::theme::Palette;
use crate::ui::message_box::{Answer, Icon, MessageBox, Role};
use crate::ui::{qt, widgets};

/// `QTimer.singleShot(1500, window.check_for_startup_updates)`.
pub const STARTUP_DELAY: Duration = Duration::from_millis(1500);

/// A status bar message for the main window: text and timeout in ms (0 stays).
pub type StatusMessage = (String, u64);

enum Pending {
    Plain,
    Available(UpdateCheck),
    Downloaded(PathBuf),
}

/// The running check, and whether it was asked for (not the startup check).
type Check = (Job<(), Result<UpdateCheck, String>>, bool);

struct DownloadDialog {
    job: Job<(u64, u64), Result<Download, String>>,
    label: String,
    progress: qt::Progress,
    /// The window grows with the label, never shrinks (`ensureSizeIsAtLeastSizeHint`).
    size: egui::Vec2,
}

pub struct Updates {
    /// The status bar's update label and its tooltip.
    pub status: String,
    pub tooltip: String,
    check: Option<Check>,
    download: Option<DownloadDialog>,
    boxes: Vec<(MessageBox, Pending)>,
    startup_at: Option<Instant>,
}

impl Default for Updates {
    fn default() -> Self {
        Self::new()
    }
}

/// Whether Lite may ask GitHub at startup (`CALLISTO_LITE_OFFLINE` keeps it quiet).
fn startup_check_allowed() -> bool {
    std::env::var_os("CALLISTO_LITE_OFFLINE").is_none_or(|v| v.is_empty() || v == "0")
}

impl Updates {
    pub fn new() -> Self {
        Updates {
            status: "Updates: not checked".into(),
            tooltip: "Updates: not checked".into(),
            check: None,
            download: None,
            boxes: Vec::new(),
            startup_at: None,
        }
    }

    /// Schedules the startup check.
    pub fn schedule_startup(&mut self) {
        if startup_check_allowed() {
            self.startup_at = Some(Instant::now() + STARTUP_DELAY);
        }
    }

    /// The check is running (Check for Updates... is disabled meanwhile).
    pub fn checking(&self) -> bool {
        self.check.is_some()
    }

    fn set_status(&mut self, text: &str, tooltip: &str) {
        self.status = text.trim().to_owned();
        self.tooltip = if tooltip.trim().is_empty() { self.status.clone() } else { tooltip.trim().to_owned() };
    }

    /// `check_for_app_updates`.
    pub fn check(&mut self, ctx: &egui::Context, interactive: bool) -> Vec<StatusMessage> {
        if self.check.is_some() {
            return vec![("Update check is already running...".into(), 3000)];
        }
        self.set_status("Updates: checking...", "Checking GitHub releases for updates.");
        let job = Job::spawn(ctx, |_| updates::check_for_updates(crate::APP_VERSION).map_err(|e| e.to_string()));
        self.check = Some((job, interactive));
        vec![("Checking for updates...".into(), 0)]
    }

    /// `_on_update_check_finished`.
    fn finished(&mut self, result: Result<UpdateCheck, String>, interactive: bool) -> Vec<StatusMessage> {
        let keep = if interactive { 5000 } else { 0 };
        match result {
            Err(e) => {
                let detail = if e.trim().is_empty() { "Unknown error.".to_owned() } else { e.trim().to_owned() };
                self.set_status("Updates: check failed", &detail);
                if interactive {
                    self.boxes.push((
                        MessageBox::ok(
                            Icon::Warning,
                            "Update Check Failed",
                            &format!("Could not check for updates.\n\n{detail}"),
                        ),
                        Pending::Plain,
                    ));
                }
                vec![("Update check failed.".into(), keep)]
            }
            Ok(r) if r.update_available => {
                let latest = if r.latest_version.trim().is_empty() {
                    "new version".to_owned()
                } else {
                    r.latest_version.trim().to_owned()
                };
                self.set_status(&format!("Updates: v{latest} available"), &format!("Update available: v{latest}"));
                if interactive {
                    self.boxes.push((available_box(&r), Pending::Available(r)));
                }
                vec![(format!("Update available: v{latest}"), keep)]
            }
            Ok(_) => {
                let v = crate::APP_VERSION;
                self.set_status("Updates: up to date", &format!("You are already using the latest version (v{v})."));
                if interactive {
                    self.boxes.push((up_to_date_box(), Pending::Plain));
                }
                vec![("You are already using the latest version.".into(), keep)]
            }
        }
    }

    /// `_start_update_download`.
    fn start_download(&mut self, ctx: &egui::Context, r: &UpdateCheck) {
        if self.download.is_some() {
            self.boxes.push((
                MessageBox::ok(Icon::Information, "Update Download", "An update download is already in progress."),
                Pending::Plain,
            ));
            return;
        }
        let url = r.download_url.trim().to_owned();
        if url.is_empty() {
            self.boxes.push((
                MessageBox::ok(Icon::Warning, "Update Download", "No download URL is available for this update."),
                Pending::Plain,
            ));
            return;
        }
        let name = updates::suggest_filename(&url, &r.latest_version, crate::APP_VERSION);
        let Some(path) = rfd::FileDialog::new()
            .set_title("Save Update Installer")
            .set_directory(super::default_download_dir())
            .set_file_name(&name)
            .add_filter(
                "Installer files (*.exe *.msi *.dmg *.pkg *.deb *.appimage *.zip *.tar.gz)",
                &["exe", "msi", "dmg", "pkg", "deb", "appimage", "zip", "gz"],
            )
            .add_filter("All files (*)", &["*"])
            .save_file()
        else {
            return;
        };
        if path.is_dir() {
            self.boxes.push((
                MessageBox::ok(Icon::Warning, "Invalid Path", "Please choose a file path, not a folder."),
                Pending::Plain,
            ));
            return;
        }
        let job = Job::spawn(ctx, move |rep| {
            let flag = rep.cancel_flag();
            let mut progress = |done: u64, total: u64| rep.send((done, total));
            updates::download(&url, &path, &mut progress, &|| flag.load(std::sync::atomic::Ordering::Relaxed))
                .map_err(|e| e.to_string())
        });
        self.download = Some(DownloadDialog {
            job,
            label: "Downloading update...".into(),
            progress: qt::Progress::busy(),
            size: egui::Vec2::ZERO,
        });
    }

    /// Runs the check and the download, and shows their windows. Returns
    /// the status bar messages.
    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> Vec<StatusMessage> {
        let mut out = Vec::new();
        if let Some(at) = self.startup_at {
            if Instant::now() >= at {
                self.startup_at = None;
                out.extend(self.check(ctx, false));
            } else {
                ctx.request_repaint_after(at - Instant::now());
            }
        }
        if let Some((job, interactive)) = &mut self.check {
            let interactive = *interactive;
            if let (_, Some(result)) = job.poll() {
                self.check = None;
                out.extend(self.finished(result, interactive));
            }
        }
        if let Some(d) = &mut self.download {
            let (msgs, done) = d.job.poll();
            for (done_bytes, total) in msgs {
                if total > 0 {
                    d.progress = qt::Progress {
                        min: 0,
                        max: total as i64,
                        value: done_bytes.min(total) as i64,
                        format: "%p%".into(),
                    };
                    let pct = 100.0 * done_bytes as f64 / total as f64;
                    d.label = format!(
                        "Downloading update... {pct:.1}% ({} / {})",
                        updates::format_bytes(done_bytes),
                        updates::format_bytes(total)
                    );
                } else {
                    d.progress = qt::Progress::busy();
                    d.label = if done_bytes > 0 {
                        format!("Downloading update... {}", updates::format_bytes(done_bytes))
                    } else {
                        "Downloading update...".into()
                    };
                }
            }
            if let Some(result) = done {
                self.download = None;
                match result {
                    Ok(Download::Done(path)) => {
                        out.push(("Update downloaded successfully.".into(), 5000));
                        self.boxes.push((downloaded_box(&path), Pending::Downloaded(path)));
                    }
                    Ok(Download::Cancelled) => out.push(("Update download cancelled.".into(), 5000)),
                    Err(e) => {
                        out.push(("Update download failed.".into(), 5000));
                        let msg = if e.trim().is_empty() { "Unknown error.".to_owned() } else { e };
                        self.boxes.push((
                            MessageBox::ok(
                                Icon::Critical,
                                "Update Download Failed",
                                &format!("Could not download the update.\n\n{msg}"),
                            ),
                            Pending::Plain,
                        ));
                    }
                }
            } else if download_dialog(ctx, d, p) {
                d.job.request_cancel();
            }
        }
        if let Some((b, _)) = self.boxes.first_mut()
            && let Some(answer) = b.show(ctx, "update_box", p)
        {
            let (b, pending) = self.boxes.remove(0);
            let label = match answer {
                Answer::Button(i) => b.buttons.get(i).map(|(l, _)| l.clone()),
                Answer::Closed => None,
            };
            match (pending, label.as_deref()) {
                (Pending::Available(r), Some("Download")) => self.start_download(ctx, &r),
                (Pending::Available(r), Some("Release Page")) => {
                    if open::that_detached(r.release_url.trim()).is_err() {
                        self.boxes.push((
                            MessageBox::ok(
                                Icon::Warning,
                                "Open URL Failed",
                                &format!("Could not open URL:\n{}", r.release_url.trim()),
                            ),
                            Pending::Plain,
                        ));
                    }
                }
                (Pending::Downloaded(path), Some("Open File")) => {
                    if open::that_detached(&path).is_err() {
                        self.boxes.push((
                            MessageBox::ok(
                                Icon::Warning,
                                "Open File Failed",
                                &format!("Could not open file:\n{}", path.display()),
                            ),
                            Pending::Plain,
                        ));
                    }
                }
                (Pending::Downloaded(path), Some("Open Folder")) => {
                    let folder = path.parent().map(PathBuf::from).unwrap_or(path);
                    if open::that_detached(&folder).is_err() {
                        self.boxes.push((
                            MessageBox::ok(
                                Icon::Warning,
                                "Open Folder Failed",
                                &format!("Could not open folder:\n{}", folder.display()),
                            ),
                            Pending::Plain,
                        ));
                    }
                }
                _ => {}
            }
        }
        out
    }
}

/// `_show_update_available_dialog`.
pub fn available_box(r: &UpdateCheck) -> MessageBox {
    let mut lines =
        vec![format!("Current version: v{}", r.current_version), format!("Latest version: v{}", r.latest_version)];
    if !r.published_at.is_empty() {
        lines.push(format!("Published: {}", r.published_at.chars().take(10).collect::<String>()));
    }
    let mut b = MessageBox::new(
        Icon::Information,
        "Update Available",
        &format!("A newer version of {} is available.", crate::APP_NAME),
    )
    .informative(&lines.join("\n"));
    let notes = updates::release_notes_preview(&r.notes, 1400);
    if !notes.is_empty() {
        b = b.details(&notes);
    }
    b = b.button("Download", Role::Accept);
    if !r.release_url.is_empty() && r.release_url != r.download_url {
        b = b.button("Release Page", Role::Action);
    }
    b.button("Close", Role::Reject)
}

pub fn up_to_date_box() -> MessageBox {
    MessageBox::ok(
        Icon::Information,
        "Up to Date",
        &format!("You are using the latest version.\n\nCurrent: v{}", crate::APP_VERSION),
    )
}

/// `_on_update_download_finished`'s box.
pub fn downloaded_box(path: &std::path::Path) -> MessageBox {
    MessageBox::new(Icon::Information, "Update Downloaded", "The update was downloaded successfully.")
        .informative(&format!(
            "Saved to:\n{}\n\nClose the app and run this installer/package to complete the update.",
            path.display()
        ))
        .button("Open File", Role::Action)
        .button("Open Folder", Role::Action)
        .button("OK", Role::Accept)
}

/// The progress dialog's size (`QProgressDialog::sizeHint`).
fn progress_size(ctx: &egui::Context, label: &str) -> egui::Vec2 {
    let w = ctx
        .fonts_mut(|f| {
            f.layout_no_wrap(
                label.to_owned(),
                crate::fonts::ui_font(qt::FONT_PX, Weight::Regular),
                egui::Color32::WHITE,
            )
            .size()
            .x
        })
        .ceil();
    vec2((w + 22.0).max(200.0), 11.0 * 2.0 + 26.0 + qt::LINE + 6.0 + 46.0 + 6.0)
}

/// The dialog's contents at `size` (`QProgressDialogPrivate::layout`, with
/// the spacing halved as it is at this height). Returns true for Cancel.
pub fn download_dialog_body(
    ui: &mut egui::Ui,
    label: &str,
    progress: &qt::Progress,
    cancel_enabled: bool,
    p: &Palette,
) -> bool {
    let full = ui.max_rect();
    let (sp, mb, bar_h) = (3.0, 5.0, 22.0);
    let cancel_w = widgets::text_width(ui, "Cancel", qt::FONT_PX, Weight::Regular).ceil() + 28.0;
    let cancel_h = 46.0;
    let cancel =
        Rect::from_min_size(pos2(full.right() - 11.0 - cancel_w, full.bottom() - mb - 41.0), vec2(cancel_w, cancel_h));
    let lh = full.height() - mb - bar_h - sp - (41.0 + sp);
    let label_rect = Rect::from_min_size(pos2(full.left() + 11.0, full.top()), vec2(full.width() - 22.0, lh));
    widgets::text_center(ui, label_rect.center(), label, qt::FONT_PX, Weight::Regular, p.text);
    let bar = Rect::from_min_size(pos2(full.left() + 11.0, full.top() + lh + sp), vec2(full.width() - 22.0, bar_h));
    qt::progress_bar(ui, bar, progress, p);
    qt::button(ui, cancel, Id::new("update_download_cancel"), "Cancel", false, cancel_enabled, p).clicked()
}

fn download_dialog(ctx: &egui::Context, d: &mut DownloadDialog, p: &Palette) -> bool {
    let want = progress_size(ctx, &d.label);
    let grow = d.size != egui::Vec2::ZERO && (want.x > d.size.x || want.y > d.size.y);
    let size = if d.size == egui::Vec2::ZERO { want } else { d.size.max(want) };
    d.size = size;
    let builder = egui::ViewportBuilder::default()
        .with_title("Downloading Update")
        .with_inner_size(size)
        .with_resizable(false)
        .with_minimize_button(false)
        .with_maximize_button(false)
        .with_always_on_top();
    let bg = p.page_bg;
    let (label, progress, enabled) = (d.label.clone(), d.progress.clone(), !d.job.cancel_requested());
    let mut cancel = false;
    ctx.show_viewport_immediate(egui::ViewportId::from_hash_of("update_download"), builder, |ctx, _| {
        if ctx.input(|i| i.viewport().close_requested()) {
            ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
            cancel = true;
        }
        if grow {
            ctx.send_viewport_cmd(egui::ViewportCommand::InnerSize(size));
        }
        egui::CentralPanel::default().frame(egui::Frame::NONE.fill(bg)).show(ctx, |ui| {
            if qt::with_app_style(|| download_dialog_body(ui, &label, &progress, enabled, p)) {
                cancel = true;
            }
        });
    });
    ctx.request_repaint_after(Duration::from_millis(100));
    cancel
}
