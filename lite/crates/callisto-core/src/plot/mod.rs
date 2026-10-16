//! The full app's hardware canvas (`src/ui/widgets/accelerated_plot_widget.py`,
//! drawn by pyqtgraph 0.14.0), minus the drawing itself: axis ticks and
//! sizes, colormaps, and the spectrogram image as it appears on screen.

pub mod axis;
pub mod colormap;
pub mod image;

pub use axis::*;
pub use colormap::Colormap;
