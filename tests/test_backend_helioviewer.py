"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.Backend import helioviewer as hv

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class _FakeResp:
    def __init__(self, *, json_data=None, content=b"", headers=None, status=200, text=""):
        self._json = json_data
        self.content = content
        self.headers = headers or {}
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class _FakeSession:
    def __init__(self, *, closest_json, screenshot_resp):
        self.closest_json = closest_json
        self.screenshot_resp = screenshot_resp
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if "getClosestImage" in url:
            return _FakeResp(json_data=self.closest_json, headers={"Content-Type": "application/json"})
        if "takeScreenshot" in url:
            return self.screenshot_resp
        return _FakeResp(status=404)


def _closest_json(name="LASCO C2"):
    return {"id": 1, "date": "2026-07-01 05:24:23", "name": name, "scale": 11.9, "width": 1024, "height": 1024}


def test_latest_image_info_parses_getclosestimage():
    session = _FakeSession(closest_json=_closest_json(), screenshot_resp=_FakeResp(content=_PNG))
    info = hv.latest_image_info("C2", session=session)
    assert info.detector == "C2"
    assert info.source_id == 4
    assert info.date == datetime(2026, 7, 1, 5, 24, 23)
    assert info.scale == 11.9 and info.width == 1024


def test_source_id_maps_c3():
    session = _FakeSession(closest_json=_closest_json("LASCO C3"), screenshot_resp=_FakeResp(content=_PNG))
    info = hv.latest_image_info("C3", session=session)
    assert info.source_id == 5


def test_unsupported_detector_raises():
    with pytest.raises(ValueError):
        hv.latest_image_info("C4")


def test_build_screenshot_url_fits_full_frame():
    info = hv.HelioviewerImageInfo(
        detector="C2", source_id=4, date=datetime(2026, 7, 1, 5, 24, 23),
        name="LASCO C2", scale=11.9, width=1024, height=1024,
    )
    url, image_scale = hv.build_screenshot_url(info, size_px=512)
    # Full native frame (1024 px @ 11.9) rendered into 512 px -> 23.8 arcsec/px.
    assert abs(image_scale - 23.8) < 1e-6
    assert "takeScreenshot" in url
    assert "layers=%5B4%2C1%2C100%5D" in url or "layers=[4,1,100]" in url
    assert "display=true" in url


def test_fetch_preview_returns_png_bytes():
    session = _FakeSession(
        closest_json=_closest_json(),
        screenshot_resp=_FakeResp(content=_PNG, headers={"Content-Type": "image/png"}),
    )
    preview = hv.fetch_preview("C2", size_px=512, session=session)
    assert preview.png_bytes.startswith(b"\x89PNG")
    assert preview.info.detector == "C2"
    assert abs(preview.image_scale - 23.8) < 1e-6
    assert "takeScreenshot" in preview.image_url
    # One call to resolve the frontier, one to render the screenshot.
    assert len(session.calls) == 2


def test_fetch_preview_rejects_non_image_response():
    session = _FakeSession(
        closest_json=_closest_json(),
        screenshot_resp=_FakeResp(content=b'{"error":"x"}', headers={"Content-Type": "application/json"}, text='{"error":"x"}'),
    )
    with pytest.raises(RuntimeError):
        hv.fetch_preview("C2", session=session)
