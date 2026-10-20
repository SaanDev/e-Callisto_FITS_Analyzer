//! Lasso selections: matplotlib's `Path.contains_points` (`point_in_path_impl`
//! in `src/_path.h`, the crossings test with its exact edge rule), the
//! spectrogram cell centres the main window tests (`_lasso_mask_coordinates`)
//! and the isolated burst (`_plot_isolated_burst`).

use crate::radio::array::Array2;

/// `Path(vertices).contains_points(points)` for a polygon (closed back to
/// its first vertex, as matplotlib closes it).
pub fn contains_points(vertices: &[(f64, f64)], points: &[(f64, f64)]) -> Vec<bool> {
    let n = vertices.len();
    points
        .iter()
        .map(|&(tx, ty)| {
            if n == 0 || !(tx.is_finite() && ty.is_finite()) {
                return false;
            }
            let mut inside = false;
            let mut yflag0 = vertices[0].1 >= ty;
            for k in 0..n {
                let (vtx0, vty0) = vertices[k];
                let (vtx1, vty1) = vertices[(k + 1) % n];
                let yflag1 = vty1 >= ty;
                if yflag0 != yflag1 && (((vty1 - ty) * (vtx0 - vtx1) >= (vtx1 - tx) * (vty0 - vty1)) == yflag1) {
                    inside = !inside;
                }
                yflag0 = yflag1;
            }
            inside
        })
        .collect()
}

/// The vertices a lasso is tested with: the drawn ones, a trailing copy of
/// the first dropped (`Path(..., closed=True)` replaces it with the start).
pub fn polygon_from_lasso(verts: &[(f64, f64)]) -> Vec<(f64, f64)> {
    let mut v = verts.to_vec();
    if v.len() >= 2 {
        let (a, b) = (v[0], v[v.len() - 1]);
        // np.allclose(verts[0], verts[-1]) with its default tolerances.
        let close = |x: f64, y: f64| (x - y).abs() <= 1e-8 + 1e-5 * y.abs();
        if close(a.0, b.0) && close(a.1, b.1) {
            v.pop();
        }
    }
    v
}

/// `_rendered_axis_centers`: `count` cell centres from `start` to `stop`.
pub fn rendered_axis_centers(start: f64, stop: f64, count: usize) -> Vec<f64> {
    let step = (stop - start) / count as f64;
    (0..count).map(|i| start + (i as f64 + 0.5) * step).collect()
}

/// `on_lasso_select`'s mask over a `rows × cols` spectrogram drawn with
/// matplotlib `extent` `[x0, x1, y0, y1]` (row 0 at the top edge `y1`).
pub fn lasso_mask(verts: &[(f64, f64)], extent: [f64; 4], rows: usize, cols: usize) -> Vec<bool> {
    let polygon = polygon_from_lasso(verts);
    let x = rendered_axis_centers(extent[0], extent[1], cols);
    let y = rendered_axis_centers(extent[3], extent[2], rows);
    let points: Vec<(f64, f64)> = y.iter().flat_map(|&yy| x.iter().map(move |&xx| (xx, yy))).collect();
    contains_points(&polygon, &points)
}

/// `_plot_isolated_burst`'s data: the selected cells kept, the rest zero, gap rows NaN.
pub fn isolate(data: &Array2, mask: &[bool], gap_rows: &[bool]) -> Array2 {
    let mut out = Array2::filled(data.rows, data.cols, 0.0);
    for (i, keep) in mask.iter().enumerate() {
        if *keep {
            out.data[i] = data.data[i];
        }
    }
    for (r, gap) in gap_rows.iter().enumerate() {
        if *gap && r < out.rows {
            out.row_mut(r).fill(f32::NAN);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn square_contains_its_inside() {
        let sq = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)];
        let r = contains_points(&sq, &[(1.0, 1.0), (3.0, 1.0), (1.0, -1.0), (f64::NAN, 1.0)]);
        assert_eq!(r, vec![true, false, false, false]);
    }

    #[test]
    fn centres_split_the_extent() {
        assert_eq!(rendered_axis_centers(0.0, 4.0, 4), vec![0.5, 1.5, 2.5, 3.5]);
        assert_eq!(rendered_axis_centers(10.0, 0.0, 2), vec![7.5, 2.5]);
    }
}
