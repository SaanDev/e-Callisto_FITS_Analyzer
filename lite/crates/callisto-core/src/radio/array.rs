//! A dense 2-D array of `f32`, rows × columns, row-major — numpy's layout for
//! the full app's `(frequency, time)` spectrogram arrays.

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Array2 {
    pub rows: usize,
    pub cols: usize,
    pub data: Vec<f32>,
}

impl Array2 {
    pub fn new(rows: usize, cols: usize, data: Vec<f32>) -> Self {
        assert_eq!(rows * cols, data.len(), "array shape does not match data length");
        Self { rows, cols, data }
    }

    pub fn filled(rows: usize, cols: usize, value: f32) -> Self {
        Self { rows, cols, data: vec![value; rows * cols] }
    }

    pub fn shape(&self) -> (usize, usize) {
        (self.rows, self.cols)
    }

    pub fn is_empty(&self) -> bool {
        self.data.is_empty()
    }

    pub fn row(&self, r: usize) -> &[f32] {
        &self.data[r * self.cols..(r + 1) * self.cols]
    }

    pub fn row_mut(&mut self, r: usize) -> &mut [f32] {
        let c = self.cols;
        &mut self.data[r * c..(r + 1) * c]
    }

    pub fn get(&self, r: usize, c: usize) -> f32 {
        self.data[r * self.cols + c]
    }

    pub fn rows_iter(&self) -> impl Iterator<Item = &[f32]> {
        self.data.chunks(self.cols.max(1))
    }

    /// `arr.T`
    pub fn transposed(&self) -> Self {
        let mut out = vec![0.0; self.data.len()];
        for r in 0..self.rows {
            for c in 0..self.cols {
                out[c * self.rows + r] = self.data[r * self.cols + c];
            }
        }
        Self { rows: self.cols, cols: self.rows, data: out }
    }

    /// `arr[::-1, :]`
    pub fn flipped_rows(&self) -> Self {
        let mut data = Vec::with_capacity(self.data.len());
        for r in (0..self.rows).rev() {
            data.extend_from_slice(self.row(r));
        }
        Self { rows: self.rows, cols: self.cols, data }
    }

    /// `np.concatenate(parts, axis=1)`; all parts must have the same row count.
    pub fn concat_cols(parts: &[&Array2]) -> Self {
        let rows = parts.first().map_or(0, |p| p.rows);
        assert!(parts.iter().all(|p| p.rows == rows), "row counts differ");
        let cols: usize = parts.iter().map(|p| p.cols).sum();
        let mut data = Vec::with_capacity(rows * cols);
        for r in 0..rows {
            for p in parts {
                data.extend_from_slice(p.row(r));
            }
        }
        Self { rows, cols, data }
    }

    /// Columns `c0..c1` of every row.
    pub fn col_range(&self, c0: usize, c1: usize) -> Self {
        let (c0, c1) = (c0.min(self.cols), c1.min(self.cols).max(c0.min(self.cols)));
        let mut data = Vec::with_capacity(self.rows * (c1 - c0));
        for r in 0..self.rows {
            data.extend_from_slice(&self.row(r)[c0..c1]);
        }
        Self { rows: self.rows, cols: c1 - c0, data }
    }
}
