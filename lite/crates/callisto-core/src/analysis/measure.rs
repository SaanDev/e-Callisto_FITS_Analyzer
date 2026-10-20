//! The ruler (`src/backend/session/measurements.py`) and the drift-rate
//! estimate (`MainWindow._drift_summary`).

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct MeasurementPoint {
    pub time_s: f64,
    pub frequency_mhz: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Measurement {
    pub point1: MeasurementPoint,
    pub point2: MeasurementPoint,
    pub start: MeasurementPoint,
    pub stop: MeasurementPoint,
    pub duration_s: f64,
    pub frequency_delta_mhz: f64,
    pub slope_mhz_s: f64,
}

/// `calculate_two_point_measurement`.
pub fn two_point(a: (f64, f64), b: (f64, f64)) -> Result<Measurement, String> {
    let p = |(x, y): (f64, f64)| -> Result<MeasurementPoint, String> {
        if !x.is_finite() || !y.is_finite() {
            return Err("Measurement points must be finite.".into());
        }
        Ok(MeasurementPoint { time_s: x, frequency_mhz: y })
    };
    let (p1, p2) = (p(a)?, p(b)?);
    if (p2.time_s - p1.time_s).abs() <= 1e-9 {
        return Err("Measurement needs two points with different time values.".into());
    }
    let (start, stop) = if p1.time_s <= p2.time_s { (p1, p2) } else { (p2, p1) };
    let duration = stop.time_s - start.time_s;
    let delta = stop.frequency_mhz - start.frequency_mhz;
    Ok(Measurement {
        point1: p1,
        point2: p2,
        start,
        stop,
        duration_s: duration,
        frequency_delta_mhz: delta,
        slope_mhz_s: delta / duration,
    })
}

#[derive(Clone, Debug, PartialEq)]
pub struct DriftSummary {
    /// The points sorted by time (stable), non-finite ones dropped.
    pub points: Vec<(f64, f64)>,
    pub avg_drift_mhz_s: f64,
    pub start_frequency_mhz: f64,
    pub end_frequency_mhz: f64,
    pub duration_s: f64,
}

/// `_drift_summary`: the mean of the segment slopes between time-sorted points.
pub fn drift_summary(points: &[(f64, f64)]) -> Option<DriftSummary> {
    let mut arr: Vec<(f64, f64)> = points.iter().copied().filter(|(x, y)| x.is_finite() && y.is_finite()).collect();
    if arr.len() < 2 {
        return None;
    }
    // np.argsort(kind="mergesort") is stable.
    arr.sort_by(|a, b| a.0.total_cmp(&b.0));
    let segments: Vec<usize> = (0..arr.len() - 1).filter(|&i| (arr[i + 1].0 - arr[i].0).abs() >= 1e-12).collect();
    if segments.is_empty() {
        return None;
    }
    let rates: Vec<f64> = segments.iter().map(|&i| (arr[i + 1].1 - arr[i].1) / (arr[i + 1].0 - arr[i].0)).collect();
    let (start, end) = (segments[0], segments[segments.len() - 1] + 1);
    Some(DriftSummary {
        avg_drift_mhz_s: rates.iter().sum::<f64>() / rates.len() as f64,
        start_frequency_mhz: arr[start].1,
        end_frequency_mhz: arr[end].1,
        duration_s: arr[end].0 - arr[start].0,
        points: arr,
    })
}
