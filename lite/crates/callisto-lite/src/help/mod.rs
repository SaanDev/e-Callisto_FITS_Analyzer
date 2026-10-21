//! The About and Help menus: the user guide, Cite this Software, Report a
//! Bug, About and the update check.

pub mod bug_report;
pub mod cite;
pub mod guide;
pub mod updates;

use std::path::PathBuf;

use crate::ui::message_box::{Icon, MessageBox};

/// `_default_download_dir` / `_bug_report_default_dir`: the Downloads
/// folder, else the home folder, else the working folder.
pub fn default_download_dir() -> PathBuf {
    let home = std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from);
    let candidates = [home.as_ref().map(|h| h.join("Downloads")), home.clone(), std::env::current_dir().ok()];
    candidates.into_iter().flatten().find(|p| p.is_dir()).unwrap_or_else(|| PathBuf::from("."))
}

/// `show_about_dialog` (`QMessageBox.about`: the application icon).
pub fn about_box() -> MessageBox {
    let (name, version) = (crate::APP_NAME, crate::APP_VERSION);
    MessageBox::ok(
        Icon::App,
        &format!("About {name}"),
        &format!(
            "{name} version {version}.\n\nDeveloped by Sahan S Liyanage\n\nAstronomical and Space Science Unit\nUniversity of Colombo, Sri Lanka\n\n2026©Copyright, All Rights Reserved."
        ),
    )
}
