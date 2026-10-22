//! File → Open Project, Save Project and Save Project As
//! (`ProjectCoordinator` and the main window's project slots): one
//! `.efaproj` for every part. Lite has the radio part; the parts it does not
//! have (solar, GCS, geomagnetic, event context, composer) are kept as the
//! file held them — their sections, arrays and embedded files — and written
//! back unchanged.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use callisto_core::analysis::annotations::new_id;
use callisto_core::project::{
    Npy, PART_KEYS, ProjectError, STORAGE_EMBED, STORAGE_LINK, project_parts, project_storage, read_project,
    write_project,
};
use callisto_core::pyjson::{Json, Object};

use crate::session::Session;
use crate::session::project::is_radio_array;

/// What Lite's `meta.app_version` says: its own version, marked `-lite`
/// (3.2.0-lite), not the full app's.
pub fn app_version() -> String {
    env!("CARGO_PKG_VERSION").to_owned()
}

/// `PART_LABELS`.
pub fn part_label(key: &str) -> &str {
    match key {
        "radio" => "Radio spectrum",
        "solar" => "Solar Image Analysis",
        "gcs" => "GCS CME Fitting",
        "geomagnetic" => "Geomagnetic response",
        "event_context" => "Event context strip",
        "composer" => "Event figure",
        other => other,
    }
}

/// The parts of an opened project Lite keeps without showing.
#[derive(Clone, Debug, Default)]
pub struct PassThrough {
    /// Part key → its `meta` section.
    pub sections: BTreeMap<String, Json>,
    /// Arrays that are not the radio part's.
    pub arrays: BTreeMap<String, Npy>,
    /// Embedded member → extracted file.
    pub files: BTreeMap<String, PathBuf>,
}

impl PassThrough {
    pub fn is_empty(&self) -> bool {
        self.sections.is_empty()
    }

    /// `has_images`: a solar or GCS part, whose images Save As would ask about.
    pub fn has_images(&self) -> bool {
        self.sections.contains_key("solar") || self.sections.contains_key("gcs")
    }
}

/// The project behind the main window.
#[derive(Clone, Debug)]
pub struct ProjectState {
    pub path: Option<PathBuf>,
    /// `_project_storage`.
    pub storage: &'static str,
    pub project_id: String,
    pub passthrough: PassThrough,
    /// `Session::project_changes` when the project was last saved or opened.
    clean_at: u64,
    /// Where embedded files are unpacked (`projects_cache_root`).
    pub cache_root: PathBuf,
    /// The meta of the file last applied.
    last_meta: Object,
}

impl ProjectState {
    pub fn new(cache_root: PathBuf) -> Self {
        Self {
            path: None,
            storage: STORAGE_LINK,
            project_id: new_id(),
            passthrough: PassThrough::default(),
            clean_at: 0,
            cache_root,
            last_meta: Object::new(),
        }
    }

    /// `ECALLISTO_PROJECTS_CACHE`, else `<data dir>/projects`.
    pub fn default_cache_root(data_dir: &Path) -> PathBuf {
        match std::env::var("ECALLISTO_PROJECTS_CACHE") {
            Ok(v) if !v.trim().is_empty() => PathBuf::from(v.trim()),
            _ => data_dir.join("projects"),
        }
    }

    /// `_project_has_content`.
    pub fn has_content(&self, s: &Session) -> bool {
        s.has_data() || !self.passthrough.is_empty()
    }

    /// `_project_dirty`: something `_mark_project_dirty` counts happened.
    pub fn is_dirty(&self, s: &Session) -> bool {
        self.has_content(s) && s.project_changes != self.clean_at
    }

    pub fn set_clean(&mut self, s: &Session, path: Option<PathBuf>) {
        self.path = path;
        self.clean_at = s.project_changes;
    }

    /// `_project_default_filename`.
    pub fn default_filename(s: &Session) -> String {
        let name = s.filename();
        let stem = if name.is_empty() {
            "project".to_owned()
        } else {
            let base = name.rsplit(['/', '\\']).next().unwrap_or(name);
            match base.rfind('.') {
                Some(i) if i > 0 => base[..i].to_owned(),
                _ => base.to_owned(),
            }
        };
        format!("{stem}.efaproj")
    }

    /// `ProjectCoordinator.capture`: `(meta, arrays, files)`; files only when embedding.
    pub fn capture(
        &self,
        s: &Session,
        storage: &'static str,
    ) -> (Object, BTreeMap<String, Npy>, BTreeMap<String, PathBuf>) {
        let mut meta = Object::new();
        let mut arrays = BTreeMap::new();
        let mut files = BTreeMap::new();
        let mut flags: BTreeMap<String, Json> =
            PART_KEYS.iter().map(|k| ((*k).to_owned(), Json::Bool(false))).collect();
        if let Some((radio, radio_arrays)) = s.project_capture() {
            meta.extend(radio);
            arrays.extend(radio_arrays);
            flags.insert("radio".into(), Json::Bool(true));
        }
        for (key, section) in &self.passthrough.sections {
            meta.insert(key.clone(), section.clone());
            flags.insert(key.clone(), Json::Bool(true));
        }
        arrays.extend(self.passthrough.arrays.iter().map(|(k, v)| (k.clone(), v.clone())));
        if storage == STORAGE_EMBED {
            files.extend(self.passthrough.files.iter().map(|(k, v)| (k.clone(), v.clone())));
        }
        meta.insert("parts".into(), Json::Object(flags));
        meta.insert("storage".into(), Json::Str(storage.into()));
        meta.insert("project_id".into(), Json::Str(self.project_id.clone()));
        meta.insert("app_version".into(), Json::Str(app_version()));
        (meta, arrays, files)
    }

    /// `extract_dir_for`: a clean folder named after the project's id.
    fn extract_dir_for(&self, meta: &Object) -> PathBuf {
        let id: String = meta
            .get("project_id")
            .map(Json::py_str)
            .unwrap_or_default()
            .chars()
            .filter(|c| c.is_alphanumeric())
            .collect();
        let target = self.cache_root.join(if id.is_empty() { "legacy".to_owned() } else { id });
        if target.exists() {
            let _ = std::fs::remove_dir_all(&target);
        }
        target
    }

    /// `open_project_path` (after the unsaved-changes question): reads the
    /// file, restores the radio part, keeps the others. Returns the restored
    /// part keys.
    pub fn open(&mut self, s: &mut Session, path: &Path) -> Result<Vec<String>, ProjectError> {
        let payload = read_project(path, Some(&|meta: &Object| self.extract_dir_for(meta)))?;
        let restored = self.apply(s, payload);
        self.storage = project_storage_of(&self.last_meta);
        self.set_clean(s, Some(path.to_path_buf()));
        let name = basename(path);
        let detail = if restored.is_empty() { "empty".to_owned() } else { restored.join(", ") };
        s.show_message(format!("Project loaded: {name} ({detail})"), 5000);
        s.log_analysis(format!("Loaded project: {name}"));
        Ok(restored)
    }

    /// `ProjectCoordinator.apply`: the radio part restored (or cleared), the
    /// others kept. Returns the part keys the file held.
    fn apply(&mut self, s: &mut Session, payload: callisto_core::project::ProjectPayload) -> Vec<String> {
        let meta = payload.meta;
        let flags = project_parts(&meta);
        self.project_id = meta.get("project_id").filter(|v| v.truthy()).map(Json::py_str).unwrap_or_else(new_id);
        let mut restored = Vec::new();
        if flags["radio"] {
            s.project_apply(&meta, &payload.arrays);
            restored.push("radio".to_owned());
        } else if s.has_data() || !s.filename().is_empty() {
            // `_clear_radio_for_project`.
            s.project_apply(&Object::new(), &BTreeMap::new());
        }
        let mut passthrough = PassThrough::default();
        for key in PART_KEYS.iter().filter(|k| **k != "radio" && flags[**k]) {
            if let Some(section @ Json::Object(_)) = meta.get(*key) {
                passthrough.sections.insert((*key).to_owned(), section.clone());
                restored.push((*key).to_owned());
            }
        }
        if !passthrough.sections.is_empty() {
            passthrough.arrays = payload.arrays.into_iter().filter(|(k, _)| !is_radio_array(k)).collect();
            passthrough.files = payload.files;
        }
        self.passthrough = passthrough;
        self.last_meta = meta;
        restored
    }

    /// `_load_snapshot_path`: a snapshot restored as an untitled, changed project.
    pub fn recover(&mut self, s: &mut Session, path: &Path) -> Result<(), ProjectError> {
        let payload = read_project(path, None)?;
        self.apply(s, payload);
        self.storage = project_storage_of(&self.last_meta);
        self.set_clean(s, None);
        s.mark_project_dirty();
        let name = basename(path);
        s.show_message(format!("Recovered session from {name}"), 5000);
        s.log_analysis(format!("Recovered snapshot: {name}"));
        Ok(())
    }

    /// `_perform_autosave`: a link-mode snapshot while there are unsaved changes.
    pub fn autosave(&self, s: &mut Session, root: &Path) -> Option<Result<PathBuf, ProjectError>> {
        if !self.is_dirty(s) {
            return None;
        }
        let (meta, arrays, _files) = self.capture(s, STORAGE_LINK);
        let r = callisto_core::project::recovery::save_snapshot(root, &meta, &arrays, self.path.as_deref(), "timer");
        match &r {
            Ok(_) => s.show_message("Autosaved recovery snapshot.", 2500),
            Err(e) => s.show_message(format!("Autosave failed: {e}"), 4000),
        }
        Some(r)
    }

    /// `_write_project_to`: captures, writes, and marks the project clean.
    pub fn save(&mut self, s: &mut Session, path: &Path, storage: &'static str) -> Result<(), ProjectError> {
        let (meta, arrays, files) = self.capture(s, storage);
        write_project(path, &meta, &arrays, &files, None, None)?;
        self.storage = storage;
        self.set_clean(s, Some(path.to_path_buf()));
        let name = basename(path);
        s.show_message(format!("Project saved: {name}"), 5000);
        s.log_analysis(format!("Saved project: {name}"));
        // The log entry above is the session's own; it does not dirty the project.
        self.clean_at = s.project_changes;
        Ok(())
    }

    /// `new_project`: a Save As copy gets its own identity.
    pub fn new_identity(&mut self) {
        self.project_id = new_id();
    }

    /// Reset All and a project without parts: nothing kept.
    pub fn clear(&mut self) {
        self.passthrough = PassThrough::default();
        self.path = None;
        self.storage = STORAGE_LINK;
    }
}

fn project_storage_of(meta: &Object) -> &'static str {
    project_storage(meta)
}

fn basename(path: &Path) -> String {
    path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()
}

/// The project file's own error text (`ProjectFormatError`), else
/// "Could not open project:" with the cause.
pub fn open_error_text(e: &ProjectError) -> String {
    match e {
        ProjectError::Format(m) => m.clone(),
        other => format!("Could not open project:\n{other}"),
    }
}
