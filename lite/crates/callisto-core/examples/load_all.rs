//! Tries to load every FITS file in a folder and lists the ones that fail.
//!
//! `cargo run --release -p callisto-core --example load_all -- <folder>`
//! Output: one `FAIL <name>: <reason>` line per failure, then a summary.
//! `lite/parity/load_all.py` does the same with the full app's loader.

use std::path::PathBuf;

use callisto_core::callisto::CallistoFile;

fn main() {
    let dir = PathBuf::from(std::env::args().nth(1).expect("usage: load_all <folder>"));
    let mut paths: Vec<PathBuf> = std::fs::read_dir(&dir)
        .expect("reading folder")
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| {
            let n = p.file_name().unwrap_or_default().to_string_lossy().to_lowercase();
            n.ends_with(".fit") || n.ends_with(".fits") || n.ends_with(".fit.gz") || n.ends_with(".fits.gz")
        })
        .collect();
    paths.sort();
    let start = std::time::Instant::now();
    let mut failed = 0;
    for p in &paths {
        if let Err(e) = CallistoFile::load(p) {
            failed += 1;
            println!("FAIL {}: {e:#}", p.file_name().unwrap().to_string_lossy());
        }
    }
    println!("{} files, {failed} failed, {:.1} s", paths.len(), start.elapsed().as_secs_f64());
}
