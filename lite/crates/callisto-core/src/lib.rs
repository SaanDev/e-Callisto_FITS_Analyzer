//! Data handling for e-CALLISTO FITS Analyzer Lite.
//!
//! No GUI code lives here, so everything can be tested headless and compared
//! with the full app's Python results (see `lite/parity`).

pub mod analysis;
pub mod archive;
pub mod callisto;
pub mod colormap;
pub mod dataset;
pub mod export;
pub mod fits;
pub mod image;
pub mod mpl;
pub mod plot;
pub mod radio;
pub mod render;
pub mod util;
pub mod xlsx;
