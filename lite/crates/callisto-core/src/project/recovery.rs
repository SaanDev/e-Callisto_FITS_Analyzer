//! Autosave snapshots (`src/backend/session/recovery_manager.py`): link-mode
//! projects in a `recovery` folder, the newest ten kept.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use super::{Npy, ProjectError, now_iso, write_project};
use crate::pyjson::{Json, Object};

/// `DEFAULT_MAX_SNAPSHOTS`.
pub const DEFAULT_MAX_SNAPSHOTS: usize = 10;

/// `list_snapshots`: the snapshots in `root`, newest first.
pub fn list_snapshots(root: &Path) -> Vec<(PathBuf, std::time::SystemTime)> {
    let Ok(entries) = std::fs::read_dir(root) else { return Vec::new() };
    let mut out: Vec<(PathBuf, std::time::SystemTime)> = entries
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|x| x == "efaproj"))
        .filter_map(|p| Some((p.clone(), std::fs::metadata(&p).ok()?.modified().ok()?)))
        .collect();
    out.sort_by_key(|e| std::cmp::Reverse(e.1));
    out
}

/// `latest_snapshot_path`.
pub fn latest_snapshot(root: &Path) -> Option<PathBuf> {
    list_snapshots(root).into_iter().next().map(|(p, _)| p)
}

/// `prune_snapshots`: all but the newest `keep`.
pub fn prune_snapshots(root: &Path, keep: usize) -> usize {
    list_snapshots(root).into_iter().skip(keep).filter(|(p, _)| std::fs::remove_file(p).is_ok()).count()
}

/// `save_recovery_snapshot`: `autosave_<UTC stamp>.efaproj`, marked as a
/// snapshot with when, why and of which project.
pub fn save_snapshot(
    root: &Path,
    meta: &Object,
    arrays: &BTreeMap<String, Npy>,
    source_project_path: Option<&Path>,
    reason: &str,
) -> Result<PathBuf, ProjectError> {
    std::fs::create_dir_all(root)?;
    let stamp = chrono::Utc::now().format("%Y%m%dT%H%M%S%6fZ");
    let path = root.join(format!("autosave_{stamp}.efaproj"));
    let mut meta = meta.clone();
    meta.insert("recovery_snapshot".into(), Json::Bool(true));
    meta.insert("recovery_saved_at".into(), now_iso().into());
    meta.insert("recovery_reason".into(), Json::Str(if reason.is_empty() { "timer" } else { reason }.into()));
    meta.insert(
        "recovery_source_project_path".into(),
        source_project_path.map_or(Json::Null, |p| Json::Str(p.to_string_lossy().into_owned())),
    );
    write_project(&path, &meta, arrays, &BTreeMap::new(), None, None)?;
    prune_snapshots(root, DEFAULT_MAX_SNAPSHOTS);
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_the_newest_ten() {
        let root = std::env::temp_dir().join(format!("callisto_recovery_test_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let meta = Object::new();
        let mut last = PathBuf::new();
        for _ in 0..12 {
            last = save_snapshot(&root, &meta, &BTreeMap::new(), None, "timer").unwrap();
            std::thread::sleep(std::time::Duration::from_millis(15));
        }
        assert_eq!(list_snapshots(&root).len(), DEFAULT_MAX_SNAPSHOTS);
        assert_eq!(latest_snapshot(&root), Some(last.clone()));
        let read = super::super::read_project_meta(&last).unwrap();
        assert_eq!(read.get("recovery_reason"), Some(&Json::Str("timer".into())));
        let _ = std::fs::remove_dir_all(&root);
    }
}
