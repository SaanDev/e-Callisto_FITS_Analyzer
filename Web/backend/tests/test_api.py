from __future__ import annotations

import gzip
from io import BytesIO
from pathlib import Path

from astropy.io import fits
from fastapi.testclient import TestClient
import numpy as np
import pytest

from app.core.config import Settings
from app.main import create_app


def make_test_fits_bytes() -> bytes:
    data = np.arange(24, dtype=np.float32).reshape(4, 6)
    primary = fits.PrimaryHDU(data=data)
    primary.header["TIME-OBS"] = "12:34:56"
    cols = fits.ColDefs(
        [
            fits.Column(name="FREQUENCY", format="4E", array=[np.array([10, 20, 30, 40], dtype=np.float32)]),
            fits.Column(name="TIME", format="6E", array=[np.arange(6, dtype=np.float32)]),
        ]
    )
    table = fits.BinTableHDU.from_columns(cols)
    buf = BytesIO()
    fits.HDUList([primary, table]).writeto(buf, overwrite=True)
    return buf.getvalue()


def make_test_fit_gz_bytes() -> bytes:
    return gzip.compress(make_test_fits_bytes())


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(
        Settings(
            runtime_dir=tmp_path / "runtime",
            frontend_origin="http://localhost:5173",
            requests_per_minute=50,
            session_creations_per_minute=50,
            dataset_uploads_per_ten_minutes=50,
            session_ttl_seconds=3600,
        )
    )
    return TestClient(app)


def test_full_api_flow(client: TestClient):
    session = client.post("/api/v1/sessions")
    assert session.status_code == 200
    session_id = session.json()["sessionId"]

    upload = client.post(
        f"/api/v1/sessions/{session_id}/dataset",
        files={"dataset": ("sample.fit", make_test_fits_bytes(), "application/fits")},
    )
    assert upload.status_code == 200
    assert upload.json()["rawSpectrum"]["shape"] == [4, 6]

    reduced = client.post(
        f"/api/v1/sessions/{session_id}/processing/background",
        json={"clipLow": -2, "clipHigh": 2},
    )
    assert reduced.status_code == 200
    assert reduced.json()["label"] == "Background Subtracted"

    maxima = client.post(
        f"/api/v1/sessions/{session_id}/analysis/maxima",
        json={"source": "processed"},
    )
    assert maxima.status_code == 200
    points = maxima.json()["points"]
    assert len(points) == 6

    trimmed_points = points[1:5]
    analysis = client.post(
        f"/api/v1/sessions/{session_id}/analysis/fit",
        json={"points": trimmed_points, "mode": "fundamental", "fold": 2},
    )
    assert analysis.status_code == 200
    assert "fit" in analysis.json()

    figure = client.post(
        f"/api/v1/sessions/{session_id}/exports/figure",
        json={"source": "processed", "plotKind": "spectrum", "format": "png", "title": "Processed"},
    )
    assert figure.status_code == 200
    assert figure.headers["content-type"].startswith("image/png")

    fits_export = client.post(
        f"/api/v1/sessions/{session_id}/exports/fits",
        json={"source": "processed", "bitpix": "auto"},
    )
    assert fits_export.status_code == 200
    assert fits_export.headers["content-type"] == "application/fits"

    csv_export = client.post(
        f"/api/v1/sessions/{session_id}/exports/maxima-csv",
        json={"points": trimmed_points},
    )
    assert csv_export.status_code == 200
    assert "Frequency (MHz)" in csv_export.text

    xlsx_export = client.post(
        f"/api/v1/sessions/{session_id}/exports/analyzer-xlsx",
        json={"analysisResult": analysis.json(), "sourceFilename": "SRI-Lanka_20260101_000000_demo.fits"},
    )
    assert xlsx_export.status_code == 200
    assert xlsx_export.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_gzipped_fit_upload_is_supported(client: TestClient):
    session_id = client.post("/api/v1/sessions").json()["sessionId"]
    upload = client.post(
        f"/api/v1/sessions/{session_id}/dataset",
        files={"dataset": ("sample.fit.gz", make_test_fit_gz_bytes(), "application/gzip")},
    )
    assert upload.status_code == 200
    assert upload.json()["filename"] == "sample.fit.gz"

    reduced = client.post(
        f"/api/v1/sessions/{session_id}/processing/background",
        json={"clipLow": -2, "clipHigh": 2},
    )
    assert reduced.status_code == 200

    fits_export = client.post(
        f"/api/v1/sessions/{session_id}/exports/fits",
        json={"source": "processed", "bitpix": "auto"},
    )
    assert fits_export.status_code == 200
    assert 'filename="sample_background_subtracted.fit"' in fits_export.headers["content-disposition"]


def test_invalid_fits_is_rejected(client: TestClient):
    session_id = client.post("/api/v1/sessions").json()["sessionId"]
    response = client.post(
        f"/api/v1/sessions/{session_id}/dataset",
        files={"dataset": ("broken.fits", b"not-a-fits", "application/fits")},
    )
    assert response.status_code == 400


def test_expired_session_is_rejected(tmp_path: Path):
    app = create_app(
        Settings(
            runtime_dir=tmp_path / "runtime",
            session_ttl_seconds=0,
            requests_per_minute=50,
            session_creations_per_minute=50,
            dataset_uploads_per_ten_minutes=50,
        )
    )
    client = TestClient(app)
    session_id = client.post("/api/v1/sessions").json()["sessionId"]
    response = client.post(
        f"/api/v1/sessions/{session_id}/analysis/maxima",
        json={"source": "raw"},
    )
    assert response.status_code == 410


def test_upload_limit_is_enforced(tmp_path: Path):
    app = create_app(
        Settings(
            runtime_dir=tmp_path / "runtime",
            max_upload_bytes=16,
            requests_per_minute=50,
            session_creations_per_minute=50,
            dataset_uploads_per_ten_minutes=50,
        )
    )
    client = TestClient(app)
    session_id = client.post("/api/v1/sessions").json()["sessionId"]
    response = client.post(
        f"/api/v1/sessions/{session_id}/dataset",
        files={"dataset": ("sample.fits", make_test_fits_bytes(), "application/fits")},
    )
    assert response.status_code == 413


def test_rate_limit_is_enforced(tmp_path: Path):
    app = create_app(
        Settings(
            runtime_dir=tmp_path / "runtime",
            requests_per_minute=1,
            session_creations_per_minute=5,
            dataset_uploads_per_ten_minutes=5,
        )
    )
    client = TestClient(app)
    session_id = client.post("/api/v1/sessions").json()["sessionId"]
    client.post(
        f"/api/v1/sessions/{session_id}/dataset",
        files={"dataset": ("sample.fits", make_test_fits_bytes(), "application/fits")},
    )
    limited = client.post(
        f"/api/v1/sessions/{session_id}/analysis/maxima",
        json={"source": "raw"},
    )
    assert limited.status_code == 429
