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
    let website = crate::APP_WEBSITE.trim_end_matches('/');
    MessageBox::ok(
        Icon::App,
        &format!("About {name}"),
        &format!(
            "{name} version {version}.\n\nDeveloped by Sahan S Liyanage\n\nAstronomical and Space Science Unit\nUniversity of Colombo, Sri Lanka\n\nWebsite: {website}\n\n2026©Copyright, All Rights Reserved."
        ),
    )
}

/// Help → Official Website: the site in the browser, else a box saying why not.
pub fn open_website() -> Option<MessageBox> {
    let url = crate::APP_WEBSITE;
    open::that_detached(url).err().map(|e| {
        MessageBox::ok(
            Icon::Warning,
            "Open URL Failed",
            &format!("Could not open the website automatically.\n\nURL:\n{url}\n\n{e}"),
        )
    })
}

#[cfg(test)]
mod tests {
    use crate::menus::{Command, Item, MenuState, menu_bar};

    #[test]
    fn website_in_about_and_help() {
        assert_eq!(crate::APP_WEBSITE, "https://ecallistoanalyzer.org/");
        assert!(super::about_box().text.contains("\n\nWebsite: https://ecallistoanalyzer.org\n\n"));
        let menus = menu_bar(&MenuState::default());
        let help = menus.iter().find(|m| m.title == "Help").unwrap();
        let items: Vec<_> = help
            .items
            .iter()
            .filter_map(|i| match i {
                Item::Action { label, command, .. } => Some((label.as_ref(), *command)),
                _ => None,
            })
            .collect();
        assert_eq!(items, [("User Guide", Command::UserGuide), ("Official Website", Command::OfficialWebsite)]);
    }
}
