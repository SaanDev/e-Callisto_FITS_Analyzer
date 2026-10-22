"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

import requests

import src.backend.services.update_checker as update_checker


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
    assert update_checker.normalize_version("v2.2.1") == (2, 2, 1)
    assert update_checker.normalize_version("release-2.2.3") == (2, 2, 3)
    assert update_checker.normalize_version("2.6.0") == (2, 6, 0)
    assert update_checker.normalize_version("invalid") == ()


def test_is_newer_version():
    assert update_checker.is_newer_version("2.0", "2.2.1")
    assert update_checker.is_newer_version("2.2.1", "2.6.0")
    assert not update_checker.is_newer_version("2.6.0", "2.2.1")
    assert not update_checker.is_newer_version("2.2.1", "2.2.1")
    assert not update_checker.is_newer_version("2.3", "2.2.1")


def test_is_newer_version_treats_prerelease_as_older_than_final():
    # beta -> final of the same version is an update.
    assert update_checker.is_newer_version("3.1.0-beta", "3.1.0")
    assert update_checker.is_newer_version("v3.1.0-beta", "v3.1.0")
    assert update_checker.is_newer_version("3.1-beta", "3.1.0")
    # final -> beta, and beta -> same beta, are not.
    assert not update_checker.is_newer_version("3.1.0", "3.1.0-beta")
    assert not update_checker.is_newer_version("3.1.0-beta", "3.1.0-beta")
    # A higher numeric version still wins regardless of suffix.
    assert update_checker.is_newer_version("3.1.0-beta", "3.1.1")
    assert not update_checker.is_newer_version("3.1.1", "3.1.0-beta")
    # 3.1.0-beta is ahead of 3.0.0. check_for_updates never offers it: prerelease-flagged releases are skipped.
    assert update_checker.is_newer_version("3.0.0", "3.1.0-beta")
    assert not update_checker.is_newer_version("3.1.0-beta", "3.0.0")


def test_is_newer_version_recognises_prerelease_suffix_forms():
    for current in (
        "3.1.0-beta",
        "3.1.0-beta2",
        "3.1.0-rc1",
        "3.1.0-alpha",
        "3.1.0b1",
        "3.1.0rc1",
        "3.1.0-BETA",
        "3.1.0-Beta.2",
        "3.1.0 Beta",
        "v3.1.0-beta(Windows)",
    ):
        assert update_checker.is_newer_version(current, "3.1.0"), current
        assert update_checker.is_newer_version(current, "v3.1.0(Windows)"), current


def test_is_newer_version_ignores_non_prerelease_tag_noise():
    for current in ("v3.1.0(Windows)", "3.1.0_amd64", "3.1.0-build5", "Release v3.1.0 by SaanDev"):
        assert not update_checker.is_newer_version(current, "3.1.0"), current
    assert not update_checker.is_newer_version("beta", "3.1.0")
    assert not update_checker.is_newer_version("3.1.0-beta", "invalid")


def test_check_for_updates_offers_final_release_to_beta_build(monkeypatch):
    releases_payload = [
        {
            "tag_name": "v3.1.0(Windows)",
            "name": "Windows Release v3.1.0",
            "prerelease": False,
            "draft": False,
            "html_url": "https://example.com/release/windows-v3.1.0",
            "assets": [
                {
                    "name": "e-CALLISTO_FITS_Analyzer_v3.1.0_Setup.exe",
                    "browser_download_url": "https://example.com/windows-v3.1.0.exe",
                }
            ],
        },
        {
            "tag_name": "v3.1.0-beta(Windows)",
            "name": "Windows Release v3.1.0-beta",
            "prerelease": True,
            "draft": False,
            "html_url": "https://example.com/release/windows-v3.1.0-beta",
            "assets": [
                {
                    "name": "e-CALLISTO_FITS_Analyzer_v3.1.0-beta_Setup.exe",
                    "browser_download_url": "https://example.com/windows-v3.1.0-beta.exe",
                }
            ],
        },
    ]

    def fake_get(url, headers=None, timeout=0):
        return FakeResponse(releases_payload)

    monkeypatch.setattr(update_checker.requests, "get", fake_get)

    result = update_checker.check_for_updates("3.1.0-beta", system_name="Windows")
    assert result.status == "update_available"
    assert result.latest_version == "3.1.0"
    assert result.download_url == "https://example.com/windows-v3.1.0.exe"

    result = update_checker.check_for_updates("3.1.0", system_name="Windows")
    assert result.status == "up_to_date"


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
            "tag_name": "v2.2.1(Windows)",
            "name": "Windows Release v2.2.1",
            "html_url": "https://example.com/release/windows-v2.2.1",
            "published_at": "2026-02-02T12:00:00Z",
            "assets": [
                {
                    "name": "e-CALLISTO_FITS_Analyzer_v2.2.1_Setup.exe",
                    "browser_download_url": "https://example.com/windows-v2.2.1.exe",
                }
            ],
        },
        {
            "tag_name": "v2.2.1(Linux)",
            "name": "Linux Release v2.2.1",
            "html_url": "https://example.com/release/linux-v2.2.1",
            "published_at": "2026-02-01T12:00:00Z",
            "assets": [
                {
                    "name": "e-callisto-fits-analyzer_2.2.1_amd64.deb",
                    "browser_download_url": "https://example.com/linux-v2.2.1.deb",
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
    assert result.latest_version == "2.2.1"
    assert result.release_url == "https://example.com/release/linux-v2.2.1"
    assert result.download_url == "https://example.com/linux-v2.2.1.deb"


def test_check_for_updates_ignores_lite_releases(monkeypatch):
    # Lite is released from the same repository with its own version numbers.
    releases_payload = [
        {
            "tag_name": "lite-v1.0.0(Windows)",
            "name": "e-CALLISTO FITS Analyzer Lite v1.0.0",
            "html_url": "https://example.com/release/lite-v1.0.0",
            "published_at": "2026-12-01T12:00:00Z",
            "assets": [
                {
                    "name": "e-CALLISTO_FITS_Analyzer_Lite_v1.0.0_Setup.exe",
                    "browser_download_url": "https://example.com/lite-v1.0.0.exe",
                }
            ],
        },
        {
            "tag_name": "lite-v4.0.0(Windows)",
            "name": "e-CALLISTO FITS Analyzer Lite v4.0.0",
            "html_url": "https://example.com/release/lite-v4.0.0",
            "published_at": "2027-01-01T12:00:00Z",
            "assets": [],
        },
        {
            "tag_name": "v3.2.0(Windows)",
            "name": "Windows Release v3.2.0",
            "html_url": "https://example.com/release/windows-v3.2.0",
            "published_at": "2026-10-14T12:00:00Z",
            "assets": [
                {
                    "name": "e-CALLISTO_FITS_Analyzer_v3.2.0_Setup.exe",
                    "browser_download_url": "https://example.com/windows-v3.2.0.exe",
                }
            ],
        },
    ]

    def fake_get(url, headers=None, timeout=0):
        return FakeResponse(releases_payload)

    monkeypatch.setattr(update_checker.requests, "get", fake_get)

    result = update_checker.check_for_updates("3.2.0", system_name="Windows")
    assert result.status == "up_to_date"
    assert result.latest_version == "3.2.0"
    assert result.release_url == "https://example.com/release/windows-v3.2.0"


def _shared_release(version, *, lite_first=True, published="2026-11-01T12:00:00Z"):
    """One tag carrying the full app's and Lite's installers (from v3.2.0)."""
    main = [
        f"e-CALLISTO_FITS_Analyzer_v{version}_Setup.exe",
        f"e-callisto-fits-analyzer_{version}_amd64.deb",
        f"e-callisto-fits-analyzer_{version}_macOS_arm64.dmg",
    ]
    lite = [
        f"e-CALLISTO_FITS_Analyzer_v{version}-lite_Setup.exe",
        f"e-callisto-fits-analyzer_{version}-lite_amd64.deb",
        f"e-callisto-fits-analyzer_{version}-lite_macOS_arm64.dmg",
    ]
    names = lite + main if lite_first else main + lite
    return {
        "tag_name": f"v{version}",
        "name": f"e-CALLISTO FITS Analyzer v{version}",
        "html_url": f"https://example.com/release/v{version}",
        "published_at": published,
        "assets": [{"name": n, "browser_download_url": f"https://example.com/{n}"} for n in names],
    }


def test_is_lite_asset():
    assert update_checker.is_lite_asset("e-CALLISTO_FITS_Analyzer_v3.2.0-lite_Setup.exe")
    assert update_checker.is_lite_asset("e-callisto-fits-analyzer_3.3.0-beta-lite_amd64.deb")
    assert not update_checker.is_lite_asset("e-CALLISTO_FITS_Analyzer_v3.2.0_Setup.exe")
    assert not update_checker.is_lite_asset("satellite_3.2.0.exe")


def test_check_for_updates_takes_main_installer_from_shared_tag(monkeypatch):
    releases_payload = [
        _shared_release("3.2.0"),
        {
            "tag_name": "v3.1.1(Windows)",
            "name": "e-CALLISTO FITS Analyzer - Windows Release (v3.1.1)",
            "html_url": "https://example.com/release/windows-v3.1.1",
            "published_at": "2026-10-14T08:59:35Z",
            "assets": [{"name": "e-CALLISTO_FITS_Analyzer_v3.1.1_Setup.exe",
                        "browser_download_url": "https://example.com/e-CALLISTO_FITS_Analyzer_v3.1.1_Setup.exe"}],
        },
    ]
    monkeypatch.setattr(update_checker.requests, "get", lambda url, headers=None, timeout=0: FakeResponse(releases_payload))

    for system, name in (
        ("Windows", "e-CALLISTO_FITS_Analyzer_v3.2.0_Setup.exe"),
        ("Linux", "e-callisto-fits-analyzer_3.2.0_amd64.deb"),
        ("Darwin", "e-callisto-fits-analyzer_3.2.0_macOS_arm64.dmg"),
    ):
        result = update_checker.check_for_updates("3.1.1", system_name=system)
        assert result.status == "update_available", system
        assert result.latest_version == "3.2.0"
        assert result.download_url == f"https://example.com/{name}"


def test_check_for_updates_skips_tag_with_only_lite_installers(monkeypatch):
    lite_only = _shared_release("3.3.0", published="2026-12-01T12:00:00Z")
    lite_only["assets"] = [a for a in lite_only["assets"] if update_checker.is_lite_asset(a["name"])]
    releases_payload = [lite_only, _shared_release("3.2.0")]
    monkeypatch.setattr(update_checker.requests, "get", lambda url, headers=None, timeout=0: FakeResponse(releases_payload))

    result = update_checker.check_for_updates("3.2.0", system_name="Windows")
    assert result.status == "up_to_date"
    assert result.latest_version == "3.2.0"


def test_check_for_updates_ignores_newer_release_from_other_platform(monkeypatch):
    releases_payload = [
        {
            "tag_name": "v2.6.0(Windows)",
            "name": "Windows Release v2.4",
            "html_url": "https://example.com/release/windows-v2.4",
            "published_at": "2026-02-03T12:00:00Z",
            "assets": [
                {
                    "name": "e-CALLISTO_FITS_Analyzer_v2.4_Setup.exe",
                    "browser_download_url": "https://example.com/windows-v2.4.exe",
                }
            ],
        },
        {
            "tag_name": "v2.2.1(Linux)",
            "name": "Linux Release v2.2.1",
            "html_url": "https://example.com/release/linux-v2.2.1",
            "published_at": "2026-02-01T12:00:00Z",
            "assets": [
                {
                    "name": "e-callisto-fits-analyzer_2.2.1_amd64.deb",
                    "browser_download_url": "https://example.com/linux-v2.2.1.deb",
                }
            ],
        },
    ]

    def fake_get(url, headers=None, timeout=0):
        return FakeResponse(releases_payload)

    monkeypatch.setattr(update_checker.requests, "get", fake_get)

    result = update_checker.check_for_updates("2.2.1", system_name="Linux")
    assert result.status == "up_to_date"
    assert result.latest_version == "2.2.1"
    assert result.download_url == "https://example.com/linux-v2.2.1.deb"


def test_check_for_updates_returns_error_on_request_failure(monkeypatch):
    def fake_get(url, headers=None, timeout=0):
        raise requests.RequestException("network down")

    monkeypatch.setattr(update_checker.requests, "get", fake_get)

    result = update_checker.check_for_updates("2.2.1")
    assert result.status == "error"
    assert "network down" in (result.error or "")


def test_check_for_updates_returns_error_when_platform_release_missing(monkeypatch):
    releases_payload = [
        {
            "tag_name": "v2.2.1(Windows)",
            "name": "Windows Release v2.2.1",
            "prerelease": False,
            "draft": False,
            "html_url": "https://example.com/release/windows-v2.2.1",
            "assets": [
                {
                    "name": "e-CALLISTO_FITS_Analyzer_v2.2.1_Setup.exe",
                    "browser_download_url": "https://example.com/windows-v2.2.1.exe",
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
            "tag_name": "v2.6.0-beta(Linux)",
            "name": "Linux beta",
            "prerelease": True,
            "draft": False,
            "assets": [
                {
                    "name": "e-callisto-fits-analyzer_2.4_beta_amd64.deb",
                    "browser_download_url": "https://example.com/linux-v2.4-beta.deb",
                }
            ],
        },
        {
            "tag_name": "v2.2.1(Linux)",
            "name": "Linux stable v2.2.1",
            "prerelease": False,
            "draft": False,
            "html_url": "https://example.com/release/linux-v2.2.1",
            "assets": [
                {
                    "name": "e-callisto-fits-analyzer_2.2.1_amd64.deb",
                    "browser_download_url": "https://example.com/linux-v2.2.1.deb",
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
    assert result.latest_version == "2.2.1"
    assert result.download_url == "https://example.com/linux-v2.2.1.deb"
