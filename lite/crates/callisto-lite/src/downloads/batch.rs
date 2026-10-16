//! "Download Selected": every file through the shared cache and copied to
//! the chosen folder, several at a time (`DownloadTask` / `EventDownloadTask`
//! on Qt's global thread pool).

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{Receiver, channel};
use std::sync::{Arc, Mutex};

use callisto_core::radio::downloader::{Candidate, deliver_archive_file};

pub enum TaskMsg {
    /// `progress(filename, fraction, message)` (capped below 1 until copied).
    Progress(String, f64, String),
    /// `done(filename, out_path, success, error)`, plus whether it was cached.
    Done { filename: String, out_path: String, success: bool, cached: bool, error: String },
}

pub struct DownloadBatch {
    pub total: usize,
    pub done: usize,
    pub cached: usize,
    /// `(filename, error)` of each failure.
    pub failures: Vec<(String, String)>,
    pub success_paths: Vec<String>,
    pub fractions: HashMap<String, f64>,
    rx: Receiver<TaskMsg>,
}

impl DownloadBatch {
    pub fn start(ctx: &egui::Context, cache_root: &Path, files: &[Candidate], dir: &Path) -> Self {
        let (tx, rx) = channel();
        let queue: Arc<Mutex<Vec<(Candidate, PathBuf)>>> =
            Arc::new(Mutex::new(files.iter().rev().map(|c| (c.clone(), dir.join(&c.filename))).collect()));
        let threads = std::thread::available_parallelism().map_or(4, |n| n.get()).min(files.len()).max(1);
        for _ in 0..threads {
            let (queue, tx, ctx, root) = (queue.clone(), tx.clone(), ctx.clone(), cache_root.to_path_buf());
            std::thread::spawn(move || {
                loop {
                    let Some((c, out)) = queue.lock().unwrap().pop() else { break };
                    let name = c.filename.clone();
                    let _ = tx.send(TaskMsg::Progress(name.clone(), 0.0, format!("Preparing {name}...")));
                    let result = deliver_archive_file(&root, &c.url, &out, &c.filename, |fraction, message| {
                        let _ = tx.send(TaskMsg::Progress(name.clone(), fraction.clamp(0.0, 0.99), message.to_owned()));
                        ctx.request_repaint();
                    });
                    let msg = match result {
                        Ok(cached) => {
                            let _ = tx.send(TaskMsg::Progress(name.clone(), 1.0, format!("Ready: {name}")));
                            TaskMsg::Done { filename: name, out_path: out.to_string_lossy().into_owned(), success: true, cached, error: String::new() }
                        }
                        Err(e) => TaskMsg::Done { filename: name, out_path: out.to_string_lossy().into_owned(), success: false, cached: false, error: e.to_string() },
                    };
                    let _ = tx.send(msg);
                    ctx.request_repaint();
                }
            });
        }
        DownloadBatch {
            total: files.len(),
            done: 0,
            cached: 0,
            failures: Vec::new(),
            success_paths: Vec::new(),
            fractions: files.iter().map(|c| (c.filename.clone(), 0.0)).collect(),
            rx,
        }
    }

    /// Messages since the last call.
    pub fn poll(&mut self) -> Vec<TaskMsg> {
        let msgs: Vec<TaskMsg> = self.rx.try_iter().collect();
        for m in &msgs {
            match m {
                TaskMsg::Progress(name, f, _) => {
                    if let Some(v) = self.fractions.get_mut(name) {
                        *v = v.max(f.clamp(0.0, 1.0));
                    }
                }
                TaskMsg::Done { filename, out_path, success, cached, error } => {
                    self.done += 1;
                    if *success {
                        self.cached += usize::from(*cached);
                        self.success_paths.push(out_path.clone());
                    } else {
                        self.failures.push((filename.clone(), error.clone()));
                    }
                    if let Some(v) = self.fractions.get_mut(filename) {
                        *v = 1.0;
                    }
                }
            }
        }
        msgs
    }

    pub fn finished(&self) -> bool {
        self.done >= self.total
    }
}

/// `QFileDialog.getExistingDirectory(self, "Select Download Folder")`.
pub fn pick_folder(title: &str) -> Option<PathBuf> {
    rfd::FileDialog::new().set_title(title).pick_folder()
}
