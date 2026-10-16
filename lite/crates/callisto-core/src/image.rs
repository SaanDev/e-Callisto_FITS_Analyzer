//! A minimal RGBA image, so the core library does not depend on the GUI toolkit.

/// An 8-bit RGBA colour with straight (not premultiplied) alpha.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash)]
#[repr(C)]
pub struct Rgba(pub [u8; 4]);

impl Rgba {
    pub const TRANSPARENT: Rgba = Rgba([0, 0, 0, 0]);

    pub const fn rgb(r: u8, g: u8, b: u8) -> Self {
        Rgba([r, g, b, 255])
    }

    pub const fn gray(v: u8) -> Self {
        Rgba([v, v, v, 255])
    }
}

/// Row-major pixels, `size = [width, height]`.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct RgbaImage {
    pub size: [usize; 2],
    pub pixels: Vec<Rgba>,
}

impl RgbaImage {
    pub fn new(size: [usize; 2], pixels: Vec<Rgba>) -> Self {
        assert_eq!(size[0] * size[1], pixels.len(), "image size does not match pixel count");
        Self { size, pixels }
    }

    pub fn width(&self) -> usize {
        self.size[0]
    }

    pub fn height(&self) -> usize {
        self.size[1]
    }

    /// The pixels as a flat `RGBARGBA…` byte slice.
    pub fn as_bytes(&self) -> &[u8] {
        // Rgba is #[repr(C)] over [u8; 4], so the cast is exact.
        unsafe { std::slice::from_raw_parts(self.pixels.as_ptr().cast::<u8>(), self.pixels.len() * 4) }
    }
}
