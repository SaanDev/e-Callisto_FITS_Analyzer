//! Colormaps as 256-entry lookup tables.

use crate::image::Rgba;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Colormap {
    Jet,
    Viridis,
    Inferno,
    Magma,
    Plasma,
    Turbo,
    Cividis,
    Cubehelix,
    Hot,
    Gray,
}

impl Colormap {
    pub const ALL: [Colormap; 10] = [
        Colormap::Jet,
        Colormap::Viridis,
        Colormap::Inferno,
        Colormap::Magma,
        Colormap::Plasma,
        Colormap::Turbo,
        Colormap::Cividis,
        Colormap::Cubehelix,
        Colormap::Hot,
        Colormap::Gray,
    ];

    pub fn name(self) -> &'static str {
        match self {
            Colormap::Jet => "Jet (CALLISTO quicklook)",
            Colormap::Viridis => "Viridis",
            Colormap::Inferno => "Inferno",
            Colormap::Magma => "Magma",
            Colormap::Plasma => "Plasma",
            Colormap::Turbo => "Turbo",
            Colormap::Cividis => "Cividis",
            Colormap::Cubehelix => "Cubehelix",
            Colormap::Hot => "Hot",
            Colormap::Gray => "Gray",
        }
    }

    fn eval(self, t: f64) -> Rgba {
        let from = |c: colorous::Color| Rgba::rgb(c.r, c.g, c.b);
        match self {
            Colormap::Jet => {
                let ch = |x: f64| ((1.5 - (4.0 * t - x).abs()).clamp(0.0, 1.0) * 255.0) as u8;
                Rgba::rgb(ch(3.0), ch(2.0), ch(1.0))
            }
            Colormap::Hot => {
                let ch = |x: f64| ((3.0 * t - x).clamp(0.0, 1.0) * 255.0) as u8;
                Rgba::rgb(ch(0.0), ch(1.0), ch(2.0))
            }
            Colormap::Gray => {
                let v = (t * 255.0) as u8;
                Rgba::rgb(v, v, v)
            }
            Colormap::Viridis => from(colorous::VIRIDIS.eval_continuous(t)),
            Colormap::Inferno => from(colorous::INFERNO.eval_continuous(t)),
            Colormap::Magma => from(colorous::MAGMA.eval_continuous(t)),
            Colormap::Plasma => from(colorous::PLASMA.eval_continuous(t)),
            Colormap::Turbo => from(colorous::TURBO.eval_continuous(t)),
            Colormap::Cividis => from(colorous::CIVIDIS.eval_continuous(t)),
            Colormap::Cubehelix => from(colorous::CUBEHELIX.eval_continuous(t)),
        }
    }

    pub fn lut(self, reversed: bool) -> [Rgba; 256] {
        std::array::from_fn(|i| {
            let t = i as f64 / 255.0;
            self.eval(if reversed { 1.0 - t } else { t })
        })
    }
}
