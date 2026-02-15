"""
e-CALLISTO FITS Analyzer
Version 2.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

import requests

import src.Backend.update_checker as update_checker


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_normalize_version():
    assert update_checker.normalize_version("v2.1") == (2, 1)
    assert update_checker.normalize_version("release-2.1.3") == (2, 1, 3)
    assert update_checker.normalize_version("invalid") == ()


def test_is_newer_version():
    assert update_checker.is_newer_version("2.0", "2.1")
    assert update_checker.is_newer_version("2.1", "2.1.1")
    assert not update_checker.is_newer_version("2.1", "2.1")
    assert not update_checker.is_newer_version("2.2", "2.1")


def test_select_download_url_by_os():
    assets = [
        {"name": "installer.exe", "browser_download_url": "https://example.com/windows.exe"},
        {"name": "installer.dmg", "browser_download_url": "https://example.com/macos.dmg"},
        {"name": "installer.deb", "browser_download_url": "https://example.com/linux.deb"},
    ]
    assert update_checker.select_download_url(assets, system_name="Windows") == "https://example.com/windows.exe"
    assert update_checker.select_download_url(assets, system_name="Darwin") == "https://example.com/macos.dmg"
    assert update_checker.select_download_url(assets, system_name="Linux") == "https://example.com/linux.deb"


def test_check_for_updates_uses_platform_specific_release_tag(monkeypatch):
    releases_payload = [
        {
            "tag_name": "v2.2.0(Windows)",
            "name": "Windows Release v2.2",
            "html_url": "https://example.com/release/windows-v2.2",
            "published_at": "2026-02-02T12:00:00Z",
            "assets": [
                {
                    "name": "e-CALLISTO_FITS_Analyzer_v2.2_Setup.exe",
                    "browser_download_url": "https://example.com/windows-v2.2.exe",
                }
            ],
        },
        {
            "tag_name": "v2.2.0(Linux)",
            "name": "Linux Release v2.2",
            "html_url": "https://example.com/release/linux-v2.2",
            "published_at": "2026-02-01T12:00:00Z",
            "assets": [
                {
                    "name": "e-callisto-fits-analyzer_2.2_amd64.deb",
                    "browser_download_url": "https://example.com/linux-v2.2.deb",
                }
            ],
        },
    ]

    def fake_get(url, headers=None, timeout=0):
        assert url == update_checker.GITHUB_RELEASES_LIST_URL
        return FakeResponse(releases_payload)

    monkeypatch.setattr(update_checker.requests, "get", fake_get)

    result = update_checker.check_for_updates("2.0", system_name="Linux")
    assert result.status == "update_available"
    assert result.latest_version == "2.2.0"
    assert result.release_url == "https://example.com/release/linux-v2.2"
    assert result.download_url == "https://example.com/linux-v2.2.deb"


def test_check_for_updates_ignores_newer_release_from_other_platform(monkeypatch):
    releases_payload = [
        {
            "tag_name": "v2.2.0(Windows)",
            "name": "Windows Release v2.2",
            "html_url": "https://example.com/release/windows-v2.2",
            "published_at": "2026-02-03T12:00:00Z",
            "assets": [
                {
                    "name": "e-CALLISTO_FITS_Analyzer_v2.2_Setup.exe",
                    "browser_download_url": "https://example.com/windows-v2.2.exe",
                }
            ],
        },
        {
            "tag_name": "v2.1.0(Linux)",
            "name": "Linux Release v2.1",
            "html_url": "https://example.com/release/linux-v2.1",
            "published_at": "2026-02-01T12:00:00Z",
            "assets": [
                {
                    "name": "e-callisto-fits-analyzer_2.1_amd64.deb",
                    "browser_download_url": "https://example.com/linux-v2.1.deb",
                }
            ],
        },
    ]

    def fake_get(url, headers=None, timeout=0):
        return FakeResponse(releases_payload)

    monkeypatch.setattr(update_checker.requests, "get", fake_get)

    result = update_checker.check_for_updates("2.1", system_name="Linux")
    assert result.status == "up_to_date"
    assert result.latest_version == "2.1.0"
    assert result.download_url == "https://example.com/linux-v2.1.deb"


def test_check_for_updates_returns_error_on_request_failure(monkeypatch):
    def fake_get(url, headers=None, timeout=0):
        raise requests.RequestException("network down")

    monkeypatch.setattr(update_checker.requests, "get", fake_get)

    result = update_checker.check_for_updates("2.1")
    assert result.status == "error"
    assert "network down" in (result.error or "")


def test_check_for_updates_returns_error_when_platform_release_missing(monkeypatch):
    releases_payload = [
        {
            "tag_name": "v2.1.0(Windows)",
            "name": "Windows Release v2.1",
            "prerelease": False,
            "draft": False,
            "html_url": "https://example.com/release/windows-v2.1",
            "assets": [
                {
                    "name": "e-CALLISTO_FITS_Analyzer_v2.1_Setup.exe",
                    "browser_download_url": "https://example.com/windows-v2.1.exe",
                }
            ],
            "body": "Windows release only.",
        },
    ]

    def fake_get(url, headers=None, timeout=0):
        return FakeResponse(releases_payload)

    monkeypatch.setattr(update_checker.requests, "get", fake_get)

    result = update_checker.check_for_updates("2.0", system_name="Linux")
    assert result.status == "error"
    assert "platform 'linux'" in (result.error or "")


def test_check_for_updates_skips_prerelease_for_platform(monkeypatch):
    releases_payload = [
        {
            "tag_name": "v2.2.0-beta(Linux)",
            "name": "Linux beta",
            "prerelease": True,
            "draft": False,
            "assets": [
                {
                    "name": "e-callisto-fits-analyzer_2.2_beta_amd64.deb",
                    "browser_download_url": "https://example.com/linux-v2.2-beta.deb",
                }
            ],
        },
        {
            "tag_name": "v2.1.0(Linux)",
            "name": "Linux stable",
            "prerelease": False,
            "draft": False,
            "html_url": "https://example.com/release/linux-v2.1",
            "assets": [
                {
                    "name": "e-callisto-fits-analyzer_2.1_amd64.deb",
                    "browser_download_url": "https://example.com/linux-v2.1.deb",
                }
            ],
            "body": "Stable release.",
        },
    ]

    def fake_get(url, headers=None, timeout=0):
        return FakeResponse(releases_payload)

    monkeypatch.setattr(update_checker.requests, "get", fake_get)

    result = update_checker.check_for_updates("2.0", system_name="Linux")
    assert result.status == "update_available"
    assert result.latest_version == "2.1.0"
    assert result.download_url == "https://example.com/linux-v2.1.deb"
