from __future__ import annotations

import gzip
from io import BytesIO
from pathlib import Path

from astropy.io import fits
import numpy as np
from openpyxl import load_workbook
import pytest

from app.domain.analyzer import fit_analyzer
from app.domain.exports import export_analyzer_xlsx, export_fits_file, export_maxima_csv
from app.domain.fits import load_callisto_fits
from app.domain.processing import background_reduce, extract_maxima_points
from app.domain.types import DatasetBundle


def make_test_fits(path: Path) -> Path:
    data = np.arange(24, dtype=np.float32).reshape(4, 6)
    primary = fits.PrimaryHDU(data=data)
    primary.header["TIME-OBS"] = "01:02:03"
    cols = fits.ColDefs(
        [
            fits.Column(name="FREQUENCY", format="4E", array=[np.array([10, 20, 30, 40], dtype=np.float32)]),
            fits.Column(name="TIME", format="6E", array=[np.arange(6, dtype=np.float32)]),
        ]
    )
    table = fits.BinTableHDU.from_columns(cols)
    fits.HDUList([primary, table]).writeto(path, overwrite=True)
    return path
def desktop_reference(points: list[dict[str, float]], fold: int, mode: str) -> dict[str, float]:
    ordered = sorted(points, key=lambda item: item["timeChannel"])
    time_channels = np.asarray([point["timeChannel"] for point in ordered], dtype=float)
    time_seconds = np.where(time_channels * 0.25 <= 0, 1.0e-6, time_channels * 0.25)
    freq = np.asarray([point["freqMHz"] for point in ordered], dtype=float)

    from scipy.optimize import curve_fit

    def model_func(t, a, b):
        return a * t ** b

    def drift_rate(t, a, b):
        return a * b * t ** (b - 1)

    params, cov = curve_fit(model_func, time_seconds, freq, maxfev=10000)
    a, b = params
    std_errs = np.sqrt(np.diag(cov))
    predicted = model_func(time_seconds, a, b)
    residuals = freq - predicted
    freq_err = np.std(residuals)
    drift_vals = drift_rate(time_seconds, a, b)
    drift_errs = np.abs(drift_vals) * np.sqrt((std_errs[0] / a) ** 2 + (std_errs[1] / b) ** 2)
    denom = fold * 3.385
    shock_speed = (13853221.38 * np.abs(drift_vals)) / (freq * (np.log(freq ** 2 / denom) ** 2))
    shock_height = 4.32 * np.log(10) / np.log(freq ** 2 / denom)
    start_freq = np.percentile(freq, 90)
    if mode == "harmonic":
        start_freq = start_freq / 2
    idx = np.abs(freq - start_freq).argmin()
    f0 = freq[idx]
    g0 = np.log(f0 ** 2 / denom)
    shock_speed_err = (13853221.38 * drift_errs[idx]) / (f0 * (g0 ** 2))
    rp_err = abs((8.64 * np.log(10) / (f0 * (g0 ** 2))) * freq_err)
    return {
        "a": float(a),
        "b": float(b),
        "avg_shock_speed": float(np.mean(shock_speed)),
        "avg_shock_height": float(np.mean(shock_height)),
        "shock_speed_err": float(shock_speed_err),
        "rp_err": float(rp_err),
    }


def test_load_background_and_maxima(tmp_path: Path):
    path = make_test_fits(tmp_path / "sample.fits")
    loaded = load_callisto_fits(str(path))
    assert loaded.data.shape == (4, 6)
    assert loaded.ut_start_seconds == 3723.0

    reduced = background_reduce(loaded.data, clip_low=-5, clip_high=5)
    assert reduced.shape == loaded.data.shape
    assert float(reduced.max()) <= 5.0

    points = extract_maxima_points(loaded.data, loaded.freqs)
    assert len(points) == loaded.data.shape[1]
    assert points[0]["timeSeconds"] == 0.0


def test_load_gzipped_fit_and_export_name(tmp_path: Path):
    gz_path = tmp_path / "sample.fit.gz"
    raw_path = tmp_path / "sample.fit"
    make_test_fits(raw_path)
    gz_path.write_bytes(gzip.compress(raw_path.read_bytes()))
    raw_path.unlink()

    loaded = load_callisto_fits(str(gz_path))
    assert loaded.data.shape == (4, 6)

    bundle = DatasetBundle(
        filename=gz_path.name,
        source_path=gz_path,
        raw_data=loaded.data,
        freqs=loaded.freqs,
        time=loaded.time,
        header0=loaded.header0,
        ut_start_seconds=loaded.ut_start_seconds,
    )
    bundle.processed_data = background_reduce(bundle.raw_data, clip_low=-2, clip_high=2)

    _, filename, _ = export_fits_file(bundle, source="processed", bitpix="auto")
    assert filename == "sample_background_subtracted.fit"


def test_analyzer_matches_desktop_formula_fixture():
    points = [
        {"timeChannel": 1.0, "timeSeconds": 0.25, "freqMHz": 120.0},
        {"timeChannel": 2.0, "timeSeconds": 0.50, "freqMHz": 96.0},
        {"timeChannel": 3.0, "timeSeconds": 0.75, "freqMHz": 82.0},
        {"timeChannel": 4.0, "timeSeconds": 1.00, "freqMHz": 72.0},
        {"timeChannel": 5.0, "timeSeconds": 1.25, "freqMHz": 64.0},
    ]
    actual = fit_analyzer(points, mode="fundamental", fold=2)
    reference = desktop_reference(points, fold=2, mode="fundamental")
    assert actual["fit"]["a"] == pytest.approx(reference["a"], rel=1e-5)
    assert actual["fit"]["b"] == pytest.approx(reference["b"], rel=1e-5)
    assert actual["shockSummary"]["avgShockSpeedKmPerSec"] == pytest.approx(reference["avg_shock_speed"], rel=1e-5)
    assert actual["shockSummary"]["avgShockHeightRs"] == pytest.approx(reference["avg_shock_height"], rel=1e-5)
    assert actual["shockSummary"]["initialShockSpeedErrKmPerSec"] == pytest.approx(reference["shock_speed_err"], rel=1e-5)
    assert actual["shockSummary"]["initialShockHeightErrRs"] == pytest.approx(reference["rp_err"], rel=1e-5)


def test_export_files(tmp_path: Path):
    path = make_test_fits(tmp_path / "station_20260101_000000_test.fits")
    loaded = load_callisto_fits(str(path))
    bundle = DatasetBundle(
        filename=path.name,
        source_path=path,
        raw_data=loaded.data,
        freqs=loaded.freqs,
        time=loaded.time,
        header0=loaded.header0,
        ut_start_seconds=loaded.ut_start_seconds,
    )
    bundle.processed_data = background_reduce(bundle.raw_data, clip_low=-2, clip_high=2)

    fits_bytes, _, _ = export_fits_file(bundle, source="processed", bitpix="auto")
    with fits.open(BytesIO(fits_bytes)) as hdul:
        assert hdul[0].header["BUNIT"] == "Digits"
        assert hdul[0].data.shape == bundle.raw_data.shape

    csv_bytes, _, _ = export_maxima_csv(
        [{"timeChannel": 1, "timeSeconds": 0.25, "freqMHz": 42.0}]
    )
    assert "Frequency (MHz)" in csv_bytes.decode("utf-8")

    analysis = fit_analyzer(
        [
            {"timeChannel": 1.0, "timeSeconds": 0.25, "freqMHz": 120.0},
            {"timeChannel": 2.0, "timeSeconds": 0.50, "freqMHz": 96.0},
            {"timeChannel": 3.0, "timeSeconds": 0.75, "freqMHz": 82.0},
        ],
        mode="fundamental",
        fold=1,
    )
    xlsx_bytes, _, _ = export_analyzer_xlsx(analysis, source_filename=path.name)
    workbook = load_workbook(BytesIO(xlsx_bytes))
    assert workbook.active["A2"].value == "2026-01-01"
    assert workbook.active["C2"].value == analysis["equation"]
