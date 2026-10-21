//! `scipy.optimize.curve_fit` as the full app calls it: bounded, so SciPy
//! 1.16's `least_squares(method="trf")` with the exact trust-region solver, a
//! 2-point finite-difference Jacobian, `x_scale = 1`, a linear loss and
//! `ftol = xtol = gtol = 1e-8`; then the covariance from the SVD of the
//! final Jacobian. Ported line by line from `scipy/optimize/_lsq/trf.py`,
//! `common.py`, `least_squares.py`, `_numdiff.py` and `_minpack_py.py`, so
//! the iterates and the result are SciPy's own.

// The matrix code indexes rows and columns as the formulas do.
#![allow(clippy::needless_range_loop)]

/// A dense `rows × cols` matrix, row-major.
#[derive(Clone, Debug, PartialEq)]
pub struct Mat {
    pub rows: usize,
    pub cols: usize,
    pub data: Vec<f64>,
}

impl Mat {
    pub fn zeros(rows: usize, cols: usize) -> Self {
        Self { rows, cols, data: vec![0.0; rows * cols] }
    }

    pub fn at(&self, r: usize, c: usize) -> f64 {
        self.data[r * self.cols + c]
    }

    fn set(&mut self, r: usize, c: usize, v: f64) {
        self.data[r * self.cols + c] = v;
    }

    /// `J.dot(s)`.
    fn dot(&self, s: &[f64]) -> Vec<f64> {
        (0..self.rows).map(|r| (0..self.cols).map(|c| self.at(r, c) * s[c]).sum()).collect()
    }

    /// `J.T.dot(f)`.
    fn t_dot(&self, f: &[f64]) -> Vec<f64> {
        (0..self.cols).map(|c| (0..self.rows).map(|r| self.at(r, c) * f[r]).sum()).collect()
    }

    /// `J * d`, scaling column `c` by `d[c]`.
    fn scale_cols(&self, d: &[f64]) -> Mat {
        let mut out = self.clone();
        for r in 0..self.rows {
            for c in 0..self.cols {
                out.data[r * self.cols + c] *= d[c];
            }
        }
        out
    }
}

/// The thin SVD of an `m × n` matrix (`m ≥ n`, `n` small) by one-sided
/// Jacobi rotations: `A = U diag(s) Vᵀ`, `s` descending. Accurate to the
/// last few bits, like LAPACK's; signs may differ, which none of the
/// callers depend on.
pub struct Svd {
    /// `m × n`, orthonormal columns (zero where `s` is zero).
    pub u: Mat,
    pub s: Vec<f64>,
    /// `n × n`, the right singular vectors as columns.
    pub v: Mat,
}

pub fn svd(a: &Mat) -> Svd {
    let (m, n) = (a.rows, a.cols);
    // Work on the columns: w[c] is column c of A V.
    let mut w: Vec<Vec<f64>> = (0..n).map(|c| (0..m).map(|r| a.at(r, c)).collect()).collect();
    let mut v = Mat::zeros(n, n);
    for i in 0..n {
        v.set(i, i, 1.0);
    }
    let dot = |x: &[f64], y: &[f64]| -> f64 { x.iter().zip(y).map(|(p, q)| p * q).sum() };
    for _sweep in 0..60 {
        let mut rotated = false;
        for p in 0..n {
            for q in p + 1..n {
                let alpha = dot(&w[p], &w[p]);
                let beta = dot(&w[q], &w[q]);
                let gamma = dot(&w[p], &w[q]);
                if gamma == 0.0 || gamma.abs() <= f64::EPSILON * (alpha * beta).sqrt() {
                    continue;
                }
                rotated = true;
                let zeta = (beta - alpha) / (2.0 * gamma);
                let sign = if zeta >= 0.0 { 1.0 } else { -1.0 };
                let t = sign / (zeta.abs() + (1.0 + zeta * zeta).sqrt());
                let c = 1.0 / (1.0 + t * t).sqrt();
                let s = c * t;
                for r in 0..m {
                    let (x, y) = (w[p][r], w[q][r]);
                    w[p][r] = c * x - s * y;
                    w[q][r] = s * x + c * y;
                }
                for r in 0..n {
                    let (x, y) = (v.at(r, p), v.at(r, q));
                    v.set(r, p, c * x - s * y);
                    v.set(r, q, s * x + c * y);
                }
            }
        }
        if !rotated {
            break;
        }
    }
    let norms: Vec<f64> = w.iter().map(|col| dot(col, col).sqrt()).collect();
    let mut order: Vec<usize> = (0..n).collect();
    order.sort_by(|&i, &j| norms[j].total_cmp(&norms[i]));
    let mut u = Mat::zeros(m, n);
    let mut vs = Mat::zeros(n, n);
    let mut s = Vec::with_capacity(n);
    for (k, &i) in order.iter().enumerate() {
        let sigma = norms[i];
        s.push(sigma);
        for r in 0..m {
            u.set(r, k, if sigma > 0.0 { w[i][r] / sigma } else { 0.0 });
        }
        for r in 0..n {
            vs.set(r, k, v.at(r, i));
        }
    }
    Svd { u, s, v: vs }
}

fn norm(x: &[f64]) -> f64 {
    x.iter().map(|v| v * v).sum::<f64>().sqrt()
}

fn dot(x: &[f64], y: &[f64]) -> f64 {
    x.iter().zip(y).map(|(a, b)| a * b).sum()
}

fn norm_inf(x: &[f64]) -> f64 {
    x.iter().fold(0.0, |m: f64, v| m.max(v.abs()))
}

fn in_bounds(x: &[f64], lb: &[f64], ub: &[f64]) -> bool {
    x.iter().zip(lb).zip(ub).all(|((v, l), u)| v >= l && v <= u)
}

/// `find_active_constraints`: -1 on a lower bound, 1 on an upper one.
fn find_active_constraints(x: &[f64], lb: &[f64], ub: &[f64], rtol: f64) -> Vec<i32> {
    let mut active = vec![0; x.len()];
    for i in 0..x.len() {
        if rtol == 0.0 {
            if x[i] <= lb[i] {
                active[i] = -1;
            }
            if x[i] >= ub[i] {
                active[i] = 1;
            }
            continue;
        }
        let lower_dist = x[i] - lb[i];
        let upper_dist = ub[i] - x[i];
        let lower_threshold = rtol * lb[i].abs().max(1.0);
        let upper_threshold = rtol * ub[i].abs().max(1.0);
        if lb[i].is_finite() && lower_dist <= upper_dist.min(lower_threshold) {
            active[i] = -1;
        }
        if ub[i].is_finite() && upper_dist <= lower_dist.min(upper_threshold) {
            active[i] = 1;
        }
    }
    active
}

fn next_after(x: f64, toward: f64) -> f64 {
    if x.is_nan() || toward.is_nan() {
        return f64::NAN;
    }
    if x == toward {
        return toward;
    }
    if x == 0.0 {
        return if toward > 0.0 { f64::from_bits(1) } else { -f64::from_bits(1) };
    }
    let bits = x.to_bits();
    let up = (toward > x) == (x > 0.0);
    f64::from_bits(if up { bits + 1 } else { bits - 1 })
}

/// `make_strictly_feasible`.
fn make_strictly_feasible(x: &[f64], lb: &[f64], ub: &[f64], rstep: f64) -> Vec<f64> {
    let mut out = x.to_vec();
    let active = find_active_constraints(x, lb, ub, rstep);
    for i in 0..x.len() {
        if rstep == 0.0 {
            if active[i] == -1 {
                out[i] = next_after(lb[i], ub[i]);
            } else if active[i] == 1 {
                out[i] = next_after(ub[i], lb[i]);
            }
        } else if active[i] == -1 {
            out[i] = lb[i] + rstep * lb[i].abs().max(1.0);
        } else if active[i] == 1 {
            out[i] = ub[i] - rstep * ub[i].abs().max(1.0);
        }
        if out[i] < lb[i] || out[i] > ub[i] {
            out[i] = 0.5 * (lb[i] + ub[i]);
        }
    }
    out
}

/// `CL_scaling_vector`.
fn cl_scaling_vector(x: &[f64], g: &[f64], lb: &[f64], ub: &[f64]) -> (Vec<f64>, Vec<f64>) {
    let mut v = vec![1.0; x.len()];
    let mut dv = vec![0.0; x.len()];
    for i in 0..x.len() {
        if g[i] < 0.0 && ub[i].is_finite() {
            v[i] = ub[i] - x[i];
            dv[i] = -1.0;
        }
        if g[i] > 0.0 && lb[i].is_finite() {
            v[i] = x[i] - lb[i];
            dv[i] = 1.0;
        }
    }
    (v, dv)
}

/// `step_size_to_bound`: the step along `s` to the first bound, and which
/// components hit it (with the sign of `s`).
fn step_size_to_bound(x: &[f64], s: &[f64], lb: &[f64], ub: &[f64]) -> (f64, Vec<i32>) {
    let steps: Vec<f64> = (0..x.len())
        .map(|i| if s[i] != 0.0 { ((lb[i] - x[i]) / s[i]).max((ub[i] - x[i]) / s[i]) } else { f64::INFINITY })
        .collect();
    let min_step = steps.iter().copied().fold(f64::INFINITY, f64::min);
    let hits = (0..x.len())
        .map(|i| {
            if steps[i] == min_step {
                if s[i] > 0.0 {
                    1
                } else if s[i] < 0.0 {
                    -1
                } else {
                    0
                }
            } else {
                0
            }
        })
        .collect();
    (min_step, hits)
}

/// `intersect_trust_region`.
fn intersect_trust_region(x: &[f64], s: &[f64], delta: f64) -> (f64, f64) {
    let a = dot(s, s);
    let b = dot(x, s);
    let c = dot(x, x) - delta * delta;
    let d = (b * b - a * c).sqrt();
    let q = -(b + d.copysign(b));
    let (t1, t2) = (q / a, c / q);
    if t1 < t2 { (t1, t2) } else { (t2, t1) }
}

/// `evaluate_quadratic` for one step.
fn evaluate_quadratic(j: &Mat, g: &[f64], s: &[f64], diag: &[f64]) -> f64 {
    let js = j.dot(s);
    let mut q = dot(&js, &js);
    q += s.iter().zip(diag).map(|(a, d)| a * d * a).sum::<f64>();
    let l = dot(s, g);
    0.5 * q + l
}

/// `build_quadratic_1d` with `diag`, without and with `s0`.
fn build_quadratic_1d(j: &Mat, g: &[f64], s: &[f64], diag: &[f64], s0: Option<&[f64]>) -> (f64, f64, f64) {
    let v = j.dot(s);
    let mut a = dot(&v, &v);
    a += s.iter().zip(diag).map(|(x, d)| x * d * x).sum::<f64>();
    a *= 0.5;
    let mut b = dot(g, s);
    let Some(s0) = s0 else { return (a, b, 0.0) };
    let u = j.dot(s0);
    b += dot(&u, &v);
    let mut c = 0.5 * dot(&u, &u) + dot(g, s0);
    b += s0.iter().zip(diag).zip(s).map(|((p, d), q)| p * d * q).sum::<f64>();
    c += 0.5 * s0.iter().zip(diag).map(|(p, d)| p * d * p).sum::<f64>();
    (a, b, c)
}

/// `minimize_quadratic_1d`.
fn minimize_quadratic_1d(a: f64, b: f64, lb: f64, ub: f64, c: f64) -> (f64, f64) {
    let mut t = vec![lb, ub];
    if a != 0.0 {
        let extremum = -0.5 * b / a;
        if lb < extremum && extremum < ub {
            t.push(extremum);
        }
    }
    let y: Vec<f64> = t.iter().map(|&t| t * (a * t + b) + c).collect();
    // np.argmin: the first minimum, NaN counting as the smallest.
    let mut best = 0;
    for i in 1..y.len() {
        if y[best].is_nan() {
            break;
        }
        if y[i].is_nan() || y[i] < y[best] {
            best = i;
        }
    }
    (t[best], y[best])
}

/// `solve_lsq_trust_region`, with `uf = Uᵀ f`.
fn solve_lsq_trust_region(
    n: usize,
    m: usize,
    uf: &[f64],
    s: &[f64],
    v: &Mat,
    delta: f64,
    initial_alpha: f64,
) -> (Vec<f64>, f64) {
    let phi_and_derivative = |alpha: f64, suf: &[f64]| -> (f64, f64) {
        let denom: Vec<f64> = s.iter().map(|x| x * x + alpha).collect();
        let p: Vec<f64> = suf.iter().zip(&denom).map(|(a, d)| a / d).collect();
        let p_norm = norm(&p);
        let phi = p_norm - delta;
        let phi_prime = -suf.iter().zip(&denom).map(|(a, d)| a * a / (d * d * d)).sum::<f64>() / p_norm;
        (phi, phi_prime)
    };
    let suf: Vec<f64> = s.iter().zip(uf).map(|(a, b)| a * b).collect();
    let full_rank = if m >= n { s[n - 1] > f64::EPSILON * m as f64 * s[0] } else { false };
    let v_dot = |w: &[f64]| v.dot(w);
    if full_rank {
        let p: Vec<f64> = v_dot(&uf.iter().zip(s).map(|(a, b)| a / b).collect::<Vec<_>>()).iter().map(|x| -x).collect();
        if norm(&p) <= delta {
            return (p, 0.0);
        }
    }
    let mut alpha_upper = norm(&suf) / delta;
    let mut alpha_lower = if full_rank {
        let (phi, phi_prime) = phi_and_derivative(0.0, &suf);
        -phi / phi_prime
    } else {
        0.0
    };
    // `initial_alpha` is never None from trf; a zero one with a rank-deficient J restarts.
    let mut alpha = if !full_rank && initial_alpha == 0.0 {
        (0.001 * alpha_upper).max((alpha_lower * alpha_upper).sqrt())
    } else {
        initial_alpha
    };
    for _ in 0..10 {
        if alpha < alpha_lower || alpha > alpha_upper {
            alpha = (0.001 * alpha_upper).max((alpha_lower * alpha_upper).sqrt());
        }
        let (phi, phi_prime) = phi_and_derivative(alpha, &suf);
        if phi < 0.0 {
            alpha_upper = alpha;
        }
        let ratio = phi / phi_prime;
        alpha_lower = alpha_lower.max(alpha - ratio);
        alpha -= (phi + delta) * ratio / delta;
        if phi.abs() < 0.01 * delta {
            break;
        }
    }
    let w: Vec<f64> = suf.iter().zip(s).map(|(a, b)| a / (b * b + alpha)).collect();
    let mut p: Vec<f64> = v_dot(&w).iter().map(|x| -x).collect();
    let k = delta / norm(&p);
    for x in &mut p {
        *x *= k;
    }
    (p, alpha)
}

/// `update_tr_radius`.
fn update_tr_radius(delta: f64, actual: f64, predicted: f64, step_norm: f64, bound_hit: bool) -> (f64, f64) {
    let ratio = if predicted > 0.0 {
        actual / predicted
    } else if predicted == actual && actual == 0.0 {
        1.0
    } else {
        0.0
    };
    let delta = if ratio < 0.25 {
        0.25 * step_norm
    } else if ratio > 0.75 && bound_hit {
        delta * 2.0
    } else {
        delta
    };
    (delta, ratio)
}

/// `check_termination`.
fn check_termination(df: f64, f: f64, dx_norm: f64, x_norm: f64, ratio: f64, ftol: f64, xtol: f64) -> Option<i32> {
    let ftol_satisfied = df < ftol * f && ratio > 0.25;
    let xtol_satisfied = dx_norm < xtol * (xtol + x_norm);
    match (ftol_satisfied, xtol_satisfied) {
        (true, true) => Some(4),
        (true, false) => Some(2),
        (false, true) => Some(3),
        _ => None,
    }
}

/// `select_step`.
#[allow(clippy::too_many_arguments)]
fn select_step(
    x: &[f64],
    j_h: &Mat,
    diag_h: &[f64],
    g_h: &[f64],
    mut p: Vec<f64>,
    mut p_h: Vec<f64>,
    d: &[f64],
    delta: f64,
    lb: &[f64],
    ub: &[f64],
    theta: f64,
) -> (Vec<f64>, Vec<f64>, f64) {
    let n = x.len();
    let xp: Vec<f64> = (0..n).map(|i| x[i] + p[i]).collect();
    if in_bounds(&xp, lb, ub) {
        let p_value = evaluate_quadratic(j_h, g_h, &p_h, diag_h);
        return (p, p_h, -p_value);
    }
    let (p_stride, hits) = step_size_to_bound(x, &p, lb, ub);
    let mut r_h = p_h.clone();
    for i in 0..n {
        if hits[i] != 0 {
            r_h[i] *= -1.0;
        }
    }
    let r: Vec<f64> = (0..n).map(|i| d[i] * r_h[i]).collect();
    for i in 0..n {
        p[i] *= p_stride;
        p_h[i] *= p_stride;
    }
    let x_on_bound: Vec<f64> = (0..n).map(|i| x[i] + p[i]).collect();
    let (_, to_tr) = intersect_trust_region(&p_h, &r_h, delta);
    let (to_bound, _) = step_size_to_bound(&x_on_bound, &r, lb, ub);
    let r_stride = to_bound.min(to_tr);
    let (r_stride_l, r_stride_u) = if r_stride > 0.0 {
        let l = (1.0 - theta) * p_stride / r_stride;
        let u = if r_stride == to_bound { theta * to_bound } else { to_tr };
        (l, u)
    } else {
        (0.0, -1.0)
    };
    let (mut r_out, mut r_h_out, r_value) = (r.clone(), r_h.clone(), f64::INFINITY);
    let r_value = if r_stride_l <= r_stride_u {
        let (a, b, c) = build_quadratic_1d(j_h, g_h, &r_h, diag_h, Some(&p_h));
        let (stride, value) = minimize_quadratic_1d(a, b, r_stride_l, r_stride_u, c);
        for i in 0..n {
            r_h_out[i] = r_h[i] * stride + p_h[i];
            r_out[i] = r_h_out[i] * d[i];
        }
        value
    } else {
        r_value
    };
    for i in 0..n {
        p[i] *= theta;
        p_h[i] *= theta;
    }
    let p_value = evaluate_quadratic(j_h, g_h, &p_h, diag_h);
    let ag_h: Vec<f64> = g_h.iter().map(|v| -v).collect();
    let ag: Vec<f64> = (0..n).map(|i| d[i] * ag_h[i]).collect();
    let to_tr = delta / norm(&ag_h);
    let (to_bound, _) = step_size_to_bound(x, &ag, lb, ub);
    let ag_stride = if to_bound < to_tr { theta * to_bound } else { to_tr };
    let (a, b, _) = build_quadratic_1d(j_h, g_h, &ag_h, diag_h, None);
    let (ag_stride, ag_value) = minimize_quadratic_1d(a, b, 0.0, ag_stride, 0.0);
    let ag_h: Vec<f64> = ag_h.iter().map(|v| v * ag_stride).collect();
    let ag: Vec<f64> = ag.iter().map(|v| v * ag_stride).collect();
    if p_value < r_value && p_value < ag_value {
        (p, p_h, -p_value)
    } else if r_value < p_value && r_value < ag_value {
        (r_out, r_h_out, -r_value)
    } else {
        (ag, ag_h, -ag_value)
    }
}

/// `approx_derivative(fun, x, method="2-point", f0=f, bounds=(lb, ub))`.
fn jacobian_2point(fun: &dyn Fn(&[f64]) -> Vec<f64>, x: &[f64], f0: &[f64], lb: &[f64], ub: &[f64]) -> Mat {
    let (m, n) = (f0.len(), x.len());
    let rstep = f64::EPSILON.sqrt();
    let mut h: Vec<f64> = x.iter().map(|&v| rstep * if v >= 0.0 { 1.0 } else { -1.0 } * v.abs().max(1.0)).collect();
    // `_adjust_scheme_to_bounds(x0, h, 1, "1-sided", lb, ub)`.
    let unbounded = lb.iter().all(|v| *v == f64::NEG_INFINITY) && ub.iter().all(|v| *v == f64::INFINITY);
    if !unbounded {
        for i in 0..n {
            let lower_dist = x[i] - lb[i];
            let upper_dist = ub[i] - x[i];
            let xi = x[i] + h[i];
            let violated = xi < lb[i] || xi > ub[i];
            let fitting = h[i].abs() <= lower_dist.max(upper_dist);
            if violated && fitting {
                h[i] = -h[i];
            }
            if upper_dist >= lower_dist && !fitting {
                h[i] = upper_dist;
            } else if upper_dist < lower_dist && !fitting {
                h[i] = -lower_dist;
            }
        }
    }
    let mut j = Mat::zeros(m, n);
    for i in 0..n {
        let mut x1 = x.to_vec();
        x1[i] = x[i] + h[i];
        let f1 = fun(&x1);
        let dx = (x[i] + h[i]) - x[i];
        for r in 0..m {
            j.set(r, i, (f1[r] - f0[r]) / dx);
        }
    }
    j
}

/// What `least_squares` returns that `curve_fit` uses.
#[derive(Clone, Debug)]
pub struct LsqResult {
    pub x: Vec<f64>,
    /// Half the sum of squared residuals.
    pub cost: f64,
    pub fun: Vec<f64>,
    pub jac: Mat,
    /// SciPy's status: > 0 converged, 0 out of evaluations.
    pub status: i32,
    pub nfev: usize,
}

/// `least_squares(fun, x0, bounds=(lb, ub), method="trf")` with SciPy's
/// defaults. `x0` must lie within the bounds.
pub fn least_squares_trf(
    fun: &dyn Fn(&[f64]) -> Vec<f64>,
    x0: &[f64],
    lb: &[f64],
    ub: &[f64],
    max_nfev: usize,
) -> Result<LsqResult, String> {
    let (ftol, xtol, gtol) = (1e-8, 1e-8, 1e-8);
    if lb.iter().zip(ub).any(|(l, u)| l >= u) {
        return Err("Each lower bound must be strictly less than each upper bound.".into());
    }
    if !in_bounds(x0, lb, ub) {
        return Err("Initial guess is outside of provided bounds".into());
    }
    let x0 = make_strictly_feasible(x0, lb, ub, 1e-10);
    let f0 = fun(&x0);
    if !f0.iter().all(|v| v.is_finite()) {
        return Err("Residuals are not finite in the initial point.".into());
    }
    let j0 = jacobian_2point(fun, &x0, &f0, lb, ub);
    let (m, n) = (f0.len(), x0.len());
    let scale = vec![1.0; n];
    let scale_inv = vec![1.0; n];

    let mut x = x0.clone();
    let mut f = f0;
    let mut nfev = 1usize;
    let mut j = j0;
    let mut cost = 0.5 * dot(&f, &f);
    let mut g = j.t_dot(&f);

    let (mut v, dv) = cl_scaling_vector(&x, &g, lb, ub);
    for i in 0..n {
        if dv[i] != 0.0 {
            v[i] *= scale_inv[i];
        }
    }
    let mut delta = norm(&(0..n).map(|i| x0[i] * scale_inv[i] / v[i].sqrt()).collect::<Vec<_>>());
    if delta == 0.0 {
        delta = 1.0;
    }
    let mut alpha = 0.0;
    let mut status: Option<i32> = None;
    loop {
        let (mut v, dv) = cl_scaling_vector(&x, &g, lb, ub);
        let g_norm = norm_inf(&(0..n).map(|i| g[i] * v[i]).collect::<Vec<_>>());
        if g_norm < gtol {
            status = Some(1);
        }
        if status.is_some() || nfev == max_nfev {
            break;
        }
        for i in 0..n {
            if dv[i] != 0.0 {
                v[i] *= scale_inv[i];
            }
        }
        let d: Vec<f64> = (0..n).map(|i| v[i].sqrt() * scale[i]).collect();
        let diag_h: Vec<f64> = (0..n).map(|i| g[i] * dv[i] * scale[i]).collect();
        let g_h: Vec<f64> = (0..n).map(|i| d[i] * g[i]).collect();

        let j_h = j.scale_cols(&d);
        let mut augmented = Mat::zeros(m + n, n);
        augmented.data[..m * n].copy_from_slice(&j_h.data);
        for i in 0..n {
            augmented.set(m + i, i, diag_h[i].sqrt());
        }
        let mut f_aug = f.clone();
        f_aug.resize(m + n, 0.0);
        let dec = svd(&augmented);
        let uf = dec.u.t_dot(&f_aug);
        let theta = 0.995f64.max(1.0 - g_norm);

        let mut actual_reduction = -1.0;
        let mut x_new = x.clone();
        let mut f_new = f.clone();
        let mut cost_new = cost;
        while actual_reduction <= 0.0 && nfev < max_nfev {
            let (p_h, a) = solve_lsq_trust_region(n, m, &uf, &dec.s, &dec.v, delta, alpha);
            alpha = a;
            let p: Vec<f64> = (0..n).map(|i| d[i] * p_h[i]).collect();
            let (step, step_h, predicted) = select_step(&x, &j_h, &diag_h, &g_h, p, p_h, &d, delta, lb, ub, theta);
            let trial: Vec<f64> = (0..n).map(|i| x[i] + step[i]).collect();
            x_new = make_strictly_feasible(&trial, lb, ub, 0.0);
            f_new = fun(&x_new);
            nfev += 1;
            let step_h_norm = norm(&step_h);
            if !f_new.iter().all(|v| v.is_finite()) {
                delta = 0.25 * step_h_norm;
                continue;
            }
            cost_new = 0.5 * dot(&f_new, &f_new);
            actual_reduction = cost - cost_new;
            let (delta_new, ratio) =
                update_tr_radius(delta, actual_reduction, predicted, step_h_norm, step_h_norm > 0.95 * delta);
            let step_norm = norm(&step);
            status = check_termination(actual_reduction, cost, step_norm, norm(&x), ratio, ftol, xtol);
            if status.is_some() {
                break;
            }
            alpha *= delta / delta_new;
            delta = delta_new;
        }
        if actual_reduction > 0.0 {
            x = x_new;
            f = f_new;
            cost = cost_new;
            j = jacobian_2point(fun, &x, &f, lb, ub);
            g = j.t_dot(&f);
        }
    }
    Ok(LsqResult { x, cost, fun: f, jac: j, status: status.unwrap_or(0), nfev })
}

/// `curve_fit`'s parameters and covariance from a converged `least_squares`.
pub fn covariance(res: &LsqResult) -> Mat {
    let (m, n) = (res.jac.rows, res.jac.cols);
    let dec = svd(&res.jac);
    let threshold = f64::EPSILON * m.max(n) as f64 * dec.s[0];
    let mut pcov = Mat::zeros(n, n);
    for (k, &s) in dec.s.iter().enumerate() {
        if s <= threshold {
            continue;
        }
        for r in 0..n {
            for c in 0..n {
                pcov.data[r * n + c] += dec.v.at(r, k) * dec.v.at(c, k) / (s * s);
            }
        }
    }
    if pcov.data.iter().any(|v| v.is_nan()) {
        return Mat { rows: n, cols: n, data: vec![f64::INFINITY; n * n] };
    }
    if m > n {
        let s_sq = 2.0 * res.cost / (m - n) as f64;
        for v in &mut pcov.data {
            *v *= s_sq;
        }
    } else {
        pcov.data.fill(f64::INFINITY);
    }
    pcov
}

/// `np.linalg.lstsq` for a small, full-column-rank system, by SVD with
/// numpy's `rcond`: the minimum-norm least-squares solution.
pub fn lstsq(a: &Mat, b: &[f64], rcond: f64) -> Vec<f64> {
    let dec = svd(a);
    let n = a.cols;
    let ub = dec.u.t_dot(b);
    let cutoff = rcond * dec.s.first().copied().unwrap_or(0.0);
    let mut x = vec![0.0; n];
    for (k, &s) in dec.s.iter().enumerate() {
        if s > cutoff {
            for r in 0..n {
                x[r] += dec.v.at(r, k) * ub[k] / s;
            }
        }
    }
    x
}

/// `np.polyfit(x, y, 1)`: `(slope, intercept)`, with numpy's column scaling.
pub fn polyfit_linear(x: &[f64], y: &[f64]) -> Option<(f64, f64)> {
    let m = x.len();
    if m < 2 {
        return None;
    }
    let mut lhs = Mat::zeros(m, 2);
    for (i, &xi) in x.iter().enumerate() {
        lhs.set(i, 0, xi);
        lhs.set(i, 1, 1.0);
    }
    let scale: Vec<f64> = (0..2).map(|c| (0..m).map(|r| lhs.at(r, c).powi(2)).sum::<f64>().sqrt()).collect();
    if scale.contains(&0.0) {
        return None;
    }
    let scaled = lhs.scale_cols(&[1.0 / scale[0], 1.0 / scale[1]]);
    let c = lstsq(&scaled, y, m as f64 * f64::EPSILON);
    Some((c[0] / scale[0], c[1] / scale[1]))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn svd_reconstructs() {
        let a = Mat { rows: 3, cols: 2, data: vec![1.0, 2.0, 3.0, 4.0, 5.0, 6.0] };
        let d = svd(&a);
        assert!(d.s[0] > d.s[1]);
        for r in 0..3 {
            for c in 0..2 {
                let v: f64 = (0..2).map(|k| d.u.at(r, k) * d.s[k] * d.v.at(c, k)).sum();
                assert!((v - a.at(r, c)).abs() < 1e-12, "{r},{c}: {v}");
            }
        }
        // numpy: [9.52551809, 0.51430058]
        assert!((d.s[0] - 9.525518091565107).abs() < 1e-12 && (d.s[1] - 0.5143005806586441).abs() < 1e-12);
    }

    #[test]
    fn power_law_fit_recovers_its_parameters() {
        let x: Vec<f64> = (1..=40).map(|i| i as f64 * 2.5).collect();
        let y: Vec<f64> = x.iter().map(|t| 300.0 * t.powf(-0.4)).collect();
        let fun = |p: &[f64]| -> Vec<f64> { x.iter().zip(&y).map(|(t, f)| p[0] * t.powf(-p[1]) - f).collect() };
        let r = least_squares_trf(&fun, &[100.0, 0.2], &[1e-12, 1e-9], &[f64::INFINITY, f64::INFINITY], 10000).unwrap();
        assert!(r.status > 0);
        assert!((r.x[0] - 300.0).abs() < 1e-5 && (r.x[1] - 0.4).abs() < 1e-8, "{:?}", r.x);
    }

    #[test]
    fn polyfit_matches_numpy() {
        // np.polyfit([1, 2, 3, 4], [2.1, 3.9, 6.2, 7.8], 1) -> [1.94, 0.15]
        let (s, i) = polyfit_linear(&[1.0, 2.0, 3.0, 4.0], &[2.1, 3.9, 6.2, 7.8]).unwrap();
        assert!((s - 1.94).abs() < 1e-12 && (i - 0.15).abs() < 1e-12, "{s} {i}");
    }
}
