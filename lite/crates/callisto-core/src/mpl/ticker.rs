//! matplotlib's tick locators and formatter (`matplotlib.ticker`, 3.10):
//! `MaxNLocator` (as `AutoLocator`), `AutoMinorLocator` and `ScalarFormatter`.

/// `AutoLocator`'s steps.
pub const AUTO_STEPS: [f64; 5] = [1.0, 2.0, 2.5, 5.0, 10.0];

/// `transforms.nonsingular(vmin, vmax, expander, tiny, increasing=True)`.
pub fn nonsingular(mut vmin: f64, mut vmax: f64, expander: f64, tiny: f64) -> (f64, f64) {
    if !vmin.is_finite() || !vmax.is_finite() {
        return (-expander, expander);
    }
    let mut swapped = false;
    if vmax < vmin {
        std::mem::swap(&mut vmin, &mut vmax);
        swapped = true;
    }
    let maxabs = vmin.abs().max(vmax.abs());
    if maxabs < (1e6 / tiny) * f64::MIN_POSITIVE {
        vmin = -expander;
        vmax = expander;
    } else if vmax - vmin <= maxabs * tiny {
        if vmax == 0.0 && vmin == 0.0 {
            vmin = -expander;
            vmax = expander;
        } else {
            vmin -= expander * vmin.abs();
            vmax += expander * vmax.abs();
        }
    }
    if swapped {
        // increasing=True: the result is always (low, high).
    }
    (vmin, vmax)
}

/// `scale_range(vmin, vmax, n, threshold=100)`.
fn scale_range(vmin: f64, vmax: f64, n: f64) -> (f64, f64) {
    let dv = (vmax - vmin).abs();
    let meanv = (vmax + vmin) / 2.0;
    let offset = if meanv.abs() / dv < 100.0 { 0.0 } else { 10f64.powf(meanv.abs().log10().floor()).copysign(meanv) };
    let scale = 10f64.powf((dv / n).log10().floor());
    (scale, offset)
}

/// Python's `divmod` for floats (`float_divmod`).
fn divmod(x: f64, y: f64) -> (f64, f64) {
    let mut m = x % y;
    let mut div = (x - m) / y;
    if m != 0.0 {
        if (y < 0.0) != (m < 0.0) {
            m += y;
            div -= 1.0;
        }
    } else {
        m = 0f64.copysign(y);
    }
    let floordiv = if div != 0.0 {
        let mut f = div.floor();
        if div - f > 0.5 {
            f += 1.0;
        }
        f
    } else {
        0f64.copysign(x / y)
    };
    (floordiv, m)
}

/// `_Edge_integer`.
struct Edge {
    step: f64,
    offset: f64,
}

impl Edge {
    fn closeto(&self, ms: f64, edge: f64) -> bool {
        let tol = if self.offset > 0.0 {
            let digits = (self.offset / self.step).log10();
            (10f64.powf(digits - 12.0)).clamp(1e-10, 0.4999)
        } else {
            1e-10
        };
        (ms - edge).abs() < tol
    }

    fn le(&self, x: f64) -> f64 {
        let (d, m) = divmod(x, self.step);
        if self.closeto(m / self.step, 1.0) { d + 1.0 } else { d }
    }

    fn ge(&self, x: f64) -> f64 {
        let (d, m) = divmod(x, self.step);
        if self.closeto(m / self.step, 0.0) { d } else { d + 1.0 }
    }
}

/// `MaxNLocator._raw_ticks` with `nbins` bins (already clipped as 'auto'
/// does), the given steps and `min_n_ticks = 2`.
pub fn max_n_ticks(vmin: f64, vmax: f64, nbins: usize, steps: &[f64]) -> Vec<f64> {
    let (vmin, vmax) = nonsingular(vmin, vmax, 1e-13, 1e-14);
    let nbins = nbins.max(1) as f64;
    let (scale, offset) = scale_range(vmin, vmax, nbins);
    let (vmin_, vmax_) = (vmin - offset, vmax - offset);
    // `_staircase`: 0.1 × steps[:-1], steps, 10 × steps[1].
    let mut extended: Vec<f64> = steps[..steps.len() - 1].iter().map(|s| 0.1 * s).collect();
    extended.extend_from_slice(steps);
    extended.push(10.0 * steps[1]);
    let steps: Vec<f64> = extended.iter().map(|s| s * scale).collect();
    let raw_step = (vmax_ - vmin_) / nbins;
    let istep = steps.iter().position(|&s| s >= raw_step).unwrap_or(steps.len() - 1);
    let mut ticks = Vec::new();
    for &step in steps[..=istep].iter().rev() {
        let best_vmin = (vmin_ / step).floor() * step;
        let edge = Edge { step, offset: offset.abs() };
        let low = edge.le(vmin_ - best_vmin);
        let high = edge.ge(vmax_ - best_vmin);
        ticks = (low as i64..=high as i64).map(|k| k as f64 * step + best_vmin).collect();
        let n = ticks.iter().filter(|&&t| t <= vmax_ && t >= vmin_).count();
        if n >= 2 {
            break;
        }
    }
    ticks.iter().map(|t| t + offset).collect()
}

/// `Axis.get_tick_space` for 'auto' bins: the axis length in points over
/// three (x) or two (y) label heights, clipped to 1..=9.
pub fn auto_bins(length_pt: f64, label_size_pt: f64, x_axis: bool) -> usize {
    let size = label_size_pt * if x_axis { 3.0 } else { 2.0 };
    let space = if size > 0.0 { (length_pt / size).floor() as i64 } else { 9 };
    space.clamp(1, 9) as usize
}

/// `_interval_contains_close`.
pub fn interval_contains_close(a: f64, b: f64, v: f64) -> bool {
    let (a, b) = if a > b { (b, a) } else { (a, b) };
    let rtol = (b - a) * 1e-10;
    a - rtol <= v && v <= b + rtol
}

/// Python's `round()` (ties to even).
fn py_round(v: f64) -> f64 {
    let r = v.round();
    if (v - v.trunc()).abs() == 0.5 { 2.0 * (v / 2.0).round() } else { r }
}

/// `AutoMinorLocator.__call__` with `ndivs='auto'`, minus locations that
/// duplicate major ticks (`Axis.get_minorticklocs`).
pub fn auto_minor_ticks(majors: &[f64], view: (f64, f64)) -> Vec<f64> {
    let mut majorlocs = majors.to_vec();
    majorlocs.sort_by(f64::total_cmp);
    majorlocs.dedup();
    if majorlocs.len() < 2 {
        return Vec::new();
    }
    let majorstep = majorlocs[1] - majorlocs[0];
    let mantissa = 10f64.powf(majorstep.log10().rem_euclid(1.0));
    let close = |a: f64, b: f64| (a - b).abs() <= 1e-8 + 1e-5 * b.abs();
    let ndivs = if [1.0, 2.5, 5.0, 10.0].iter().any(|&m| close(mantissa, m)) { 5.0 } else { 4.0 };
    let minorstep = majorstep / ndivs;
    let (vmin, vmax) = if view.0 <= view.1 { view } else { (view.1, view.0) };
    let t0 = majorlocs[0];
    let tmin = py_round((vmin - t0) / minorstep) as i64;
    let tmax = py_round((vmax - t0) / minorstep) as i64 + 1;
    let tol = (vmax - vmin) * 1e-5;
    (tmin..tmax).map(|k| k as f64 * minorstep + t0).filter(|m| !majors.iter().any(|j| (m - j).abs() <= tol)).collect()
}

/// `ScalarFormatter` (default settings): labels for `locs` on an axis with
/// view interval `view`, and the offset text.
pub fn scalar_format(locs: &[f64], view: (f64, f64)) -> (Vec<String>, String) {
    if locs.is_empty() {
        return (Vec::new(), String::new());
    }
    let (vmin, vmax) = if view.0 <= view.1 { view } else { (view.1, view.0) };
    let visible: Vec<f64> = locs.iter().copied().filter(|l| vmin <= *l && *l <= vmax).collect();
    let offset = compute_offset(&visible);
    let oom = order_of_magnitude(&visible, offset, vmin, vmax);
    // `_set_format`.
    let mut pts: Vec<f64> = if locs.len() < 2 { vec![locs[0], vmin, vmax] } else { locs.to_vec() };
    for p in &mut pts {
        *p = (*p - offset) / 10f64.powi(oom);
    }
    let max = pts.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let min = pts.iter().copied().fold(f64::INFINITY, f64::min);
    let mut range = max - min;
    if range == 0.0 {
        range = pts.iter().map(|p| p.abs()).fold(0.0, f64::max);
    }
    if range == 0.0 {
        range = 1.0;
    }
    if locs.len() < 2 {
        pts.truncate(1);
    }
    let range_oom = range.log10().floor() as i32;
    let mut sigfigs = (3 - range_oom).max(0);
    let thresh = 1e-3 * 10f64.powi(range_oom);
    while sigfigs >= 0 {
        let p = 10f64.powi(sigfigs);
        let worst = pts.iter().map(|v| (v - round_half_even(v * p) / p).abs()).fold(0.0, f64::max);
        if worst < thresh {
            sigfigs -= 1;
        } else {
            break;
        }
    }
    sigfigs += 1;
    let labels = locs
        .iter()
        .map(|&x| {
            let mut xp = (x - offset) / 10f64.powi(oom);
            if xp.abs() < 1e-8 {
                xp = 0.0;
            }
            minus(&format!("{:.*}", sigfigs as usize, xp))
        })
        .collect();
    let offset_text = offset_text(offset, oom);
    (labels, offset_text)
}

/// numpy's `round(decimals)`: half to even on the scaled value.
fn round_half_even(v: f64) -> f64 {
    let r = v.round();
    if (v - v.trunc()).abs() == 0.5 { 2.0 * (v / 2.0).round() } else { r }
}

/// `axes.unicode_minus`: a minus sign instead of a hyphen.
pub fn minus(s: &str) -> String {
    s.replace('-', "\u{2212}")
}

fn compute_offset(visible: &[f64]) -> f64 {
    if visible.is_empty() {
        return 0.0;
    }
    let lmin = visible.iter().copied().fold(f64::INFINITY, f64::min);
    let lmax = visible.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    if lmin == lmax || (lmin <= 0.0 && 0.0 <= lmax) {
        return 0.0;
    }
    let (abs_min, abs_max) = {
        let (a, b) = (lmin.abs(), lmax.abs());
        if a <= b { (a, b) } else { (b, a) }
    };
    let sign = 1f64.copysign(lmin);
    let oom_max = abs_max.log10().ceil() as i32;
    let fdiv = |v: f64, o: i32| (v / 10f64.powi(o)).floor();
    let mut oom = (i32::MIN..=oom_max).rev().find(|&o| fdiv(abs_min, o) != fdiv(abs_max, o)).map_or(0, |o| o + 1);
    if (abs_max - abs_min) / 10f64.powi(oom) <= 1e-2 {
        oom = (i32::MIN..=oom_max).rev().find(|&o| fdiv(abs_max, o) - fdiv(abs_min, o) > 1.0).map_or(0, |o| o + 1);
    }
    // Only when it saves at least `offset_threshold` (4) digits.
    if fdiv(abs_max, oom) >= 10f64.powi(3) { sign * fdiv(abs_max, oom) * 10f64.powi(oom) } else { 0.0 }
}

fn order_of_magnitude(visible: &[f64], offset: f64, vmin: f64, vmax: f64) -> i32 {
    if visible.is_empty() {
        return 0;
    }
    let oom = if offset != 0.0 {
        (vmax - vmin).log10().floor() as i32
    } else {
        let val = visible.iter().map(|v| v.abs()).fold(0.0, f64::max);
        if val == 0.0 { 0 } else { val.log10().floor() as i32 }
    };
    if oom <= -5 || oom >= 6 { oom } else { 0 }
}

/// `ScalarFormatter.get_offset` (plain text, no math text).
fn offset_text(offset: f64, oom: i32) -> String {
    if oom == 0 && offset == 0.0 {
        return String::new();
    }
    let mut offset_str = String::new();
    if offset != 0.0 {
        offset_str = format_data(offset);
        if offset > 0.0 {
            offset_str.insert(0, '+');
        }
    }
    let sci = if oom != 0 { format!("1e{oom}") } else { String::new() };
    minus(&format!("{sci}{offset_str}"))
}

/// `ScalarFormatter.format_data`: `1e5`, `2.5e-3`, `-3`.
fn format_data(value: f64) -> String {
    let e = value.abs().log10().floor() as i32;
    let s = (value / 10f64.powi(e) * 1e10).round() / 1e10;
    let significand = if s % 1.0 == 0.0 { format!("{}", s as i64) } else { format_g10(s) };
    if e == 0 { significand } else { format!("{significand}e{e}") }
}

/// `'%1.10g'`.
fn format_g10(v: f64) -> String {
    let s = format!("{:.9e}", v);
    let (m, e) = s.split_once('e').unwrap();
    let e: i32 = e.parse().unwrap();
    if (-4..10).contains(&e) {
        let decimals = (9 - e).max(0) as usize;
        let t = format!("{:.*}", decimals, v);
        if t.contains('.') { t.trim_end_matches('0').trim_end_matches('.').to_owned() } else { t }
    } else {
        let m = m.trim_end_matches('0').trim_end_matches('.');
        format!("{m}e{}{:02}", if e < 0 { '-' } else { '+' }, e.abs())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn auto_locator_like_matplotlib() {
        // Preview window: time 0..1797 s, 9 bins.
        let t = max_n_ticks(0.0, 1797.0, 9, &AUTO_STEPS);
        assert_eq!(t, (0..=9).map(|k| k as f64 * 200.0).collect::<Vec<_>>());
        // Colour bar −1..8.
        assert_eq!(max_n_ticks(-1.0, 8.0, 9, &AUTO_STEPS), (-1..=8).map(f64::from).collect::<Vec<_>>());
        // Frequencies 10..110.
        let f = max_n_ticks(10.0, 110.0, 9, &AUTO_STEPS);
        assert_eq!(f, (0..=6).map(|k| k as f64 * 20.0).collect::<Vec<_>>());
        assert_eq!(auto_bins(390.0, 12.0, true), 9);
        assert_eq!(auto_bins(70.0, 8.0, false), 4);
    }

    #[test]
    fn minor_ticks_and_labels() {
        let m = auto_minor_ticks(&[-2.0, 0.0, 2.0, 4.0], (-1.0, 3.0));
        assert_eq!(m, [-1.0, -0.5, 0.5, 1.0, 1.5, 2.5, 3.0]);
        let (labels, off) = scalar_format(&[-1.0, 0.0, 1.0, 2.0], (-1.0, 8.0));
        assert_eq!(labels, ["\u{2212}1", "0", "1", "2"]);
        assert_eq!(off, "");
        let (labels, _) = scalar_format(&[0.0, 0.5, 1.0], (0.0, 1.0));
        assert_eq!(labels, ["0.0", "0.5", "1.0"]);
        let (labels, off) = scalar_format(&[100000.0, 100001.0, 100002.0], (100000.0, 100002.0));
        assert_eq!((labels[1].as_str(), off.as_str()), ("1", "+1e5"));
    }
}
