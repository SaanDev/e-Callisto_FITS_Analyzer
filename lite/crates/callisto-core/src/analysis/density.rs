//! Coronal electron-density models (`src/backend/radio/density_models.py`):
//! a burst's frequency gives an emission height, its drift a speed,
//! `v = |df/dt| · |dr/df|` with `dr/df = 2n / (f |dn/dr|)`, every model scaled
//! by the fold number.

/// Solar radius in km, as the Analyzer's historical Newkirk constant implies.
pub const RSUN_KM: f64 = 696_340.0;
/// `f_p² / n` in MHz² cm³, from the Analyzer's Newkirk constant 3.385.
pub const PLASMA_FREQ_SQ_PER_DENSITY: f64 = 3.385 / 4.2e4;
pub const MIN_HEIGHT_RS: f64 = 1.0;
pub const MAX_HEIGHT_RS: f64 = 215.0;

const NEWKIRK_N0: f64 = 4.2e4;
const NEWKIRK_SCALE: f64 = 4.32;
const MANN_NS: f64 = 5.14e9;
const MANN_A: f64 = 13.83;
const BISECTION_STEPS: usize = 80;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum DensityModel {
    Newkirk,
    Saito,
    Leblanc,
    BaumbachAllen,
    Mann,
}

impl DensityModel {
    /// `DENSITY_MODEL_ORDER`.
    pub const ALL: [DensityModel; 5] = [Self::Newkirk, Self::Saito, Self::Leblanc, Self::BaumbachAllen, Self::Mann];

    pub fn key(self) -> &'static str {
        match self {
            Self::Newkirk => "newkirk",
            Self::Saito => "saito",
            Self::Leblanc => "leblanc",
            Self::BaumbachAllen => "baumbach_allen",
            Self::Mann => "mann",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Newkirk => "Newkirk",
            Self::Saito => "Saito",
            Self::Leblanc => "Leblanc",
            Self::BaumbachAllen => "Baumbach-Allen",
            Self::Mann => "Mann",
        }
    }

    pub fn reference(self) -> &'static str {
        match self {
            Self::Newkirk => "Newkirk (1961)",
            Self::Saito => "Saito, Poland & Munro (1977)",
            Self::Leblanc => "Leblanc, Dulk & Bougeret (1998)",
            Self::BaumbachAllen => "Baumbach (1937); Allen (1947)",
            Self::Mann => "Mann et al. (1999)",
        }
    }

    /// `normalize_density_model`: a key or label; anything else is Newkirk.
    pub fn parse(value: &str) -> Self {
        let text = value.trim().to_lowercase().replace(['-', ' '], "_");
        Self::ALL
            .into_iter()
            .find(|m| m.key() == text || m.label().to_lowercase().replace(['-', ' '], "_") == text)
            .unwrap_or(Self::Newkirk)
    }

    /// n(r) in cm⁻³, r in solar radii.
    pub fn density(self, r: f64) -> f64 {
        match self {
            Self::Newkirk => NEWKIRK_N0 * 10f64.powf(NEWKIRK_SCALE / r),
            Self::Saito => 1.36e6 * r.powf(-2.14) + 1.68e8 * r.powf(-6.13),
            Self::Leblanc => 3.3e5 * r.powf(-2.0) + 4.1e6 * r.powf(-4.0) + 8.0e7 * r.powf(-6.0),
            Self::BaumbachAllen => 1e8 * (2.99 * r.powf(-16.0) + 1.55 * r.powf(-6.0) + 0.036 * r.powf(-1.5)),
            Self::Mann => MANN_NS * (MANN_A * (1.0 / r - 1.0)).exp(),
        }
    }

    /// dn/dr.
    // Saito's exponent is 3.14 (one more than the model's 2.14), not π.
    #[allow(clippy::approx_constant)]
    pub fn derivative(self, r: f64) -> f64 {
        match self {
            Self::Newkirk => -self.density(r) * NEWKIRK_SCALE * std::f64::consts::LN_10 / (r * r),
            Self::Saito => -2.14 * 1.36e6 * r.powf(-3.14) - 6.13 * 1.68e8 * r.powf(-7.13),
            Self::Leblanc => -2.0 * 3.3e5 * r.powf(-3.0) - 4.0 * 4.1e6 * r.powf(-5.0) - 6.0 * 8.0e7 * r.powf(-7.0),
            Self::BaumbachAllen => {
                1e8 * (-16.0 * 2.99 * r.powf(-17.0) - 6.0 * 1.55 * r.powf(-7.0) - 1.5 * 0.036 * r.powf(-2.5))
            }
            Self::Mann => -MANN_A * self.density(r) / (r * r),
        }
    }
}

fn fold_factor(fold: f64) -> f64 {
    if fold.is_finite() && fold > 0.0 { fold } else { 1.0 }
}

/// `model_density_for_frequency`: the unscaled density a plasma frequency calls for.
pub fn model_density_for_frequency(freq_mhz: f64, fold: f64) -> f64 {
    freq_mhz * freq_mhz / (PLASMA_FREQ_SQ_PER_DENSITY * fold_factor(fold))
}

/// `_solve_height`: where the falling model reaches `target`, by bisection in log r.
fn solve_height(model: DensityModel, target: f64) -> f64 {
    let (lo, hi) = (MIN_HEIGHT_RS, MAX_HEIGHT_RS);
    let inside = target <= model.density(lo) && target >= model.density(hi) && target.is_finite();
    let (mut log_lo, mut log_hi) = (lo.ln(), hi.ln());
    for _ in 0..BISECTION_STEPS {
        let mid = 0.5 * (log_lo + log_hi);
        if model.density(mid.exp()) > target {
            log_lo = mid;
        } else {
            log_hi = mid;
        }
    }
    if inside { (0.5 * (log_lo + log_hi)).exp() } else { f64::NAN }
}

/// `height_rs_from_frequency`.
pub fn height_rs(freq_mhz: f64, model: DensityModel, fold: f64) -> f64 {
    let target = model_density_for_frequency(freq_mhz, fold);
    match model {
        DensityModel::Newkirk => NEWKIRK_SCALE * std::f64::consts::LN_10 / (target / NEWKIRK_N0).ln(),
        DensityModel::Mann => {
            let h = 1.0 / (1.0 + (target / MANN_NS).ln() / MANN_A);
            if h.is_finite() && (MIN_HEIGHT_RS..=MAX_HEIGHT_RS).contains(&h) { h } else { f64::NAN }
        }
        _ => solve_height(model, target),
    }
}

/// `height_gradient_rs_per_mhz`: |dr/df|.
pub fn height_gradient(freq_mhz: f64, model: DensityModel, fold: f64) -> f64 {
    if model == DensityModel::Newkirk {
        let g = (model_density_for_frequency(freq_mhz, fold) / NEWKIRK_N0).ln();
        return 2.0 * NEWKIRK_SCALE * std::f64::consts::LN_10 / (freq_mhz * g * g);
    }
    let h = height_rs(freq_mhz, model, fold);
    let density = model.density(h);
    let slope = model.derivative(h).abs();
    2.0 * density / (freq_mhz * slope)
}

/// `shock_speed_km_s`.
pub fn shock_speed_km_s(freq_mhz: f64, drift_mhz_s: f64, model: DensityModel, fold: f64) -> f64 {
    RSUN_KM * drift_mhz_s.abs() * height_gradient(freq_mhz, model, fold)
}

fn nan_mean(values: &[f64]) -> f64 {
    let finite: Vec<f64> = values.iter().copied().filter(|v| v.is_finite()).collect();
    if finite.is_empty() { f64::NAN } else { finite.iter().sum::<f64>() / finite.len() as f64 }
}

fn nan_sem(values: &[f64]) -> f64 {
    let finite: Vec<f64> = values.iter().copied().filter(|v| v.is_finite()).collect();
    if finite.is_empty() {
        return f64::NAN;
    }
    let n = finite.len() as f64;
    let mean = finite.iter().sum::<f64>() / n;
    let var = finite.iter().map(|v| (v - mean) * (v - mean)).sum::<f64>() / n;
    var.sqrt() / n.sqrt()
}

/// `shock_parameters`: speed and height along a fitted backbone for one model.
#[derive(Clone, Debug, PartialEq)]
pub struct ShockParameters {
    pub model: DensityModel,
    pub fold: i64,
    pub shock_speed_km_s: Vec<f64>,
    pub shock_height_rs: Vec<f64>,
    pub start_freq_mhz: f64,
    pub start_index: usize,
    pub initial_shock_speed_km_s: f64,
    pub initial_shock_speed_err_km_s: f64,
    pub initial_shock_height_rs: f64,
    pub initial_shock_height_err_rs: f64,
    pub avg_shock_speed_km_s: f64,
    pub avg_shock_speed_err_km_s: f64,
    pub avg_shock_height_rs: f64,
    pub avg_shock_height_err_rs: f64,
}

pub fn shock_parameters(
    freq_mhz: &[f64],
    drift_mhz_s: &[f64],
    drift_err_mhz_s: &[f64],
    freq_err_mhz: f64,
    model: DensityModel,
    fold: f64,
    start_percentile: f64,
) -> ShockParameters {
    let gradient: Vec<f64> = freq_mhz.iter().map(|&f| height_gradient(f, model, fold)).collect();
    let speed: Vec<f64> = drift_mhz_s.iter().zip(&gradient).map(|(d, g)| RSUN_KM * d.abs() * g).collect();
    let height: Vec<f64> = freq_mhz.iter().map(|&f| height_rs(f, model, fold)).collect();
    // np.percentile over the frequencies (NaN would propagate; there are none here).
    let mut sorted = freq_mhz.to_vec();
    sorted.sort_by(f64::total_cmp);
    let start_freq = crate::radio::numeric::percentile_sorted(&sorted, start_percentile).unwrap_or(f64::NAN);
    // np.abs(freqs - start).argmin(): the first minimum.
    let mut idx = 0;
    for (i, f) in freq_mhz.iter().enumerate() {
        if (f - start_freq).abs() < (freq_mhz[idx] - start_freq).abs() {
            idx = i;
        }
    }
    ShockParameters {
        model,
        fold: fold_factor(fold).round() as i64,
        start_freq_mhz: start_freq,
        start_index: idx,
        initial_shock_speed_km_s: speed[idx],
        initial_shock_speed_err_km_s: RSUN_KM * drift_err_mhz_s[idx] * gradient[idx],
        initial_shock_height_rs: height[idx],
        initial_shock_height_err_rs: (gradient[idx] * freq_err_mhz).abs(),
        avg_shock_speed_km_s: nan_mean(&speed),
        avg_shock_speed_err_km_s: nan_sem(&speed),
        avg_shock_height_rs: nan_mean(&height),
        avg_shock_height_err_rs: nan_sem(&height),
        shock_speed_km_s: speed,
        shock_height_rs: height,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn newkirk_matches_the_historical_constants() {
        // 3.385 MHz² per 4.2e4 cm⁻³: at fold 1 a 100 MHz burst sits at
        // 4.32 ln10 / ln(1e4 / 3.385).
        let h = height_rs(100.0, DensityModel::Newkirk, 1.0);
        let want = 4.32 * std::f64::consts::LN_10 / (1e4f64 / 3.385).ln();
        assert!((h - want).abs() < 1e-12);
        assert_eq!(DensityModel::parse("Baumbach-Allen"), DensityModel::BaumbachAllen);
        assert_eq!(DensityModel::parse("unknown"), DensityModel::Newkirk);
    }

    #[test]
    fn tabulated_models_bisect_inside_their_range() {
        for m in [DensityModel::Saito, DensityModel::Leblanc, DensityModel::BaumbachAllen] {
            let h = height_rs(50.0, m, 1.0);
            assert!(h > 1.0 && h < 215.0, "{m:?} {h}");
            let n = m.density(h);
            assert!((n - model_density_for_frequency(50.0, 1.0)).abs() / n < 1e-9);
        }
        assert!(height_rs(1e6, DensityModel::Saito, 1.0).is_nan());
    }
}
