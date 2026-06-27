"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Tests for the JSOC fast-path client (src/Backend/jsoc_client.py).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.Backend import jsoc_client as jc


# ---------------------------------------------------------------------------
# Recordset / series construction
# ---------------------------------------------------------------------------
def test_series_for_wavelength():
    assert jc.series_for_wavelength(193) == jc.SERIES_AIA_EUV
    assert jc.series_for_wavelength(171.0) == jc.SERIES_AIA_EUV
    assert jc.series_for_wavelength(1600) == jc.SERIES_AIA_UV
    with pytest.raises(jc.JsocError):
        jc.series_for_wavelength(4500)


def test_build_recordset_format():
    series, recordset = jc.build_recordset(
        start=datetime(2014, 11, 5, 9, 45, 0),
        end=datetime(2014, 11, 5, 10, 45, 0),
        wavelength_angstrom=193,
        cadence_seconds=120,
    )
    assert series == jc.SERIES_AIA_EUV
    assert recordset == "aia.lev1_euv_12s[2014-11-05T09:45:00Z/3600s@120s][193]{image}"


def test_build_recordset_defaults_cadence():
    _, recordset = jc.build_recordset(
        start=datetime(2014, 11, 5, 9, 45, 0),
        end=datetime(2014, 11, 5, 9, 55, 0),
        wavelength_angstrom=211,
        cadence_seconds=None,
    )
    assert f"@{jc.DEFAULT_CADENCE_SECONDS}s" in recordset


def test_build_recordset_rejects_bad_window():
    with pytest.raises(jc.JsocError):
        jc.build_recordset(
            start=datetime(2014, 11, 5, 10, 0, 0),
            end=datetime(2014, 11, 5, 9, 0, 0),
            wavelength_angstrom=193,
        )


# ---------------------------------------------------------------------------
# Fake drms plumbing
# ---------------------------------------------------------------------------
class _FakeRequest:
    def __init__(self, urls, *, needs_wait=False):
        self.urls = urls
        self.waited = False
        self._needs_wait = needs_wait

    def wait(self):
        self.waited = True


class _FakeClient:
    def __init__(self, urls, *, registered=True):
        self._urls = urls
        self._registered = registered
        self.export_calls = []

    def check_email(self, email):
        return self._registered

    def export(self, recordset, method=None, protocol=None, email=None, process=None):
        self.export_calls.append(
            {"recordset": recordset, "method": method, "protocol": protocol,
             "email": email, "process": process}
        )
        return _FakeRequest(self._urls, needs_wait=(method == "url"))


def test_export_urls_quick_path():
    rows = [
        {"record": "aia.lev1_euv_12s[2014-11-05T09:45:00Z][193]", "filename": "img1.fits",
         "url": "http://jsoc.example/img1.fits", "size": 4096},
        {"record": "aia.lev1_euv_12s[2014-11-05T09:47:00Z][193]", "filename": "img2.fits",
         "url": "http://jsoc.example/img2.fits"},
    ]
    client = _FakeClient(rows)
    result = jc.export_urls(
        start=datetime(2014, 11, 5, 9, 45, 0),
        end=datetime(2014, 11, 5, 10, 45, 0),
        wavelength_angstrom=193,
        email="sci@example.org",
        cadence_seconds=120,
        client=client,
    )
    assert result.record_count == 2
    assert result.urls[0].url == "http://jsoc.example/img1.fits"
    assert result.urls[0].size == 4096
    # Fast path: quick method + as-is (compressed) protocol.
    call = client.export_calls[0]
    assert call["method"] == "url_quick"
    assert call["protocol"] == "as-is"
    assert call["process"] is None


def test_export_urls_with_process_forces_staged_export():
    rows = [{"record": "r", "filename": "c.fits", "url": "http://jsoc.example/c.fits"}]
    client = _FakeClient(rows)
    jc.export_urls(
        start=datetime(2014, 11, 5, 9, 45, 0),
        end=datetime(2014, 11, 5, 10, 0, 0),
        wavelength_angstrom=193,
        email="sci@example.org",
        process={"im_patch": {"x": 0, "y": 0}},
        client=client,
    )
    call = client.export_calls[0]
    assert call["method"] == "url"          # staged, not quick
    assert call["protocol"] == "fits"       # as-is downgraded for processing
    assert call["process"] == {"im_patch": {"x": 0, "y": 0}}


def test_export_urls_requires_email():
    with pytest.raises(jc.JsocError):
        jc.export_urls(
            start=datetime(2014, 11, 5, 9, 45, 0),
            end=datetime(2014, 11, 5, 10, 0, 0),
            wavelength_angstrom=193,
            email="",
            client=_FakeClient([]),
        )


def test_export_urls_empty_raises():
    with pytest.raises(jc.JsocError):
        jc.export_urls(
            start=datetime(2014, 11, 5, 9, 45, 0),
            end=datetime(2014, 11, 5, 10, 0, 0),
            wavelength_angstrom=193,
            email="sci@example.org",
            client=_FakeClient([]),
        )


def test_check_email():
    assert jc.check_email("sci@example.org", client=_FakeClient([], registered=True)) is True
    assert jc.check_email("sci@example.org", client=_FakeClient([], registered=False)) is False
    assert jc.check_email("not-an-email", client=_FakeClient([])) is False
    assert jc.check_email("", client=_FakeClient([])) is False


def test_size_process_modes():
    assert jc.size_process(jc.SIZE_FULL) is None
    assert jc.size_process(jc.SIZE_BIN2) == {"rebin": {"method": "boxcar", "scale": 0.5}}
    assert jc.size_process(jc.SIZE_BIN4) == {"rebin": {"method": "boxcar", "scale": 0.25}}

    proc = jc.size_process(jc.SIZE_CUTOUT, cutout=(100.0, -200.0, 500.0, 400.0), t_ref="2014-11-05T09:45:00Z")
    patch = proc["im_patch"]
    assert patch["x"] == 100.0 and patch["y"] == -200.0
    assert patch["width"] == 500.0 and patch["height"] == 400.0
    assert patch["t_ref"] == "2014-11-05T09:45:00Z"


def test_size_process_cutout_requires_box():
    with pytest.raises(jc.JsocError):
        jc.size_process(jc.SIZE_CUTOUT)
    with pytest.raises(jc.JsocError):
        jc.size_process(jc.SIZE_CUTOUT, cutout=(0, 0, 0, 100))


def test_size_process_rejects_unknown_mode():
    with pytest.raises(jc.JsocError):
        jc.size_process("ultra")


def test_estimate_download_scales_with_mode():
    full_bytes, full_secs = jc.estimate_download(10, jc.SIZE_FULL)
    bin_bytes, _ = jc.estimate_download(10, jc.SIZE_BIN4)
    assert full_bytes == 10 * jc.per_frame_bytes(jc.SIZE_FULL)
    assert bin_bytes < full_bytes
    assert full_secs > 0
    assert jc.estimate_download(0, jc.SIZE_FULL) == (0, 0.0)


def test_filename_fallback_from_record():
    rows = [{"record": "aia.lev1_euv_12s[2014-11-05T09:45:00Z][193]",
             "url": "http://jsoc.example/seg?RecordSet=x"}]
    result = jc.export_urls(
        start=datetime(2014, 11, 5, 9, 45, 0),
        end=datetime(2014, 11, 5, 10, 0, 0),
        wavelength_angstrom=193,
        email="sci@example.org",
        client=_FakeClient(rows),
    )
    assert result.urls[0].filename.endswith(".fits")
    assert "[" not in result.urls[0].filename
