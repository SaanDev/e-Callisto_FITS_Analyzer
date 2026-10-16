//! Background work for the download windows, as the full app's
//! `QThread` workers do it: a thread that reports progress and a result
//! over channels, can be asked to cancel, and wakes the UI when it reports.

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{Receiver, Sender, channel};

/// Reports progress from a job's thread.
pub struct Reporter<P> {
    tx: Sender<P>,
    ctx: egui::Context,
    cancel: Arc<AtomicBool>,
}

impl<P: Send> Reporter<P> {
    pub fn send(&self, p: P) {
        let _ = self.tx.send(p);
        self.ctx.request_repaint();
    }

    pub fn cancelled(&self) -> bool {
        self.cancel.load(Ordering::Relaxed)
    }

    /// The cancel flag, for the core's `cancelled` callbacks.
    pub fn cancel_flag(&self) -> Arc<AtomicBool> {
        self.cancel.clone()
    }
}

/// A running (or finished) job.
pub struct Job<P, R> {
    progress: Receiver<P>,
    result: Receiver<R>,
    cancel: Arc<AtomicBool>,
    finished: bool,
}

impl<P: Send + 'static, R: Send + 'static> Job<P, R> {
    pub fn spawn(ctx: &egui::Context, work: impl FnOnce(&Reporter<P>) -> R + Send + 'static) -> Self {
        let (tx, progress) = channel();
        let (rtx, result) = channel();
        let cancel = Arc::new(AtomicBool::new(false));
        let reporter = Reporter { tx, ctx: ctx.clone(), cancel: cancel.clone() };
        let ctx = ctx.clone();
        std::thread::spawn(move || {
            let r = work(&reporter);
            let _ = rtx.send(r);
            ctx.request_repaint();
        });
        Job { progress, result, cancel, finished: false }
    }

    /// Progress reported since the last call, and the result once done.
    pub fn poll(&mut self) -> (Vec<P>, Option<R>) {
        let mut out: Vec<P> = self.progress.try_iter().collect();
        if self.finished {
            return (out, None);
        }
        match self.result.try_recv() {
            Ok(r) => {
                self.finished = true;
                out.extend(self.progress.try_iter());
                (out, Some(r))
            }
            Err(_) => (out, None),
        }
    }

    pub fn request_cancel(&self) {
        self.cancel.store(true, Ordering::Relaxed);
    }

    pub fn cancel_requested(&self) -> bool {
        self.cancel.load(Ordering::Relaxed)
    }

    pub fn is_finished(&self) -> bool {
        self.finished
    }
}
