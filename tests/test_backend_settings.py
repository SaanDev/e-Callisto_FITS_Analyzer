"""
e-CALLISTO FITS Analyzer
Version 2.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


import src.Backend.settings as settings


def test_settings_metadata():
    assert settings.application.endswith("e-Callisto FITS Analyzer.app")
    assert settings.volume_name == "e-Callisto FITS Analyzer"
    assert settings.format == "UDZO"


def test_settings_dmg_contents():
    assert settings.files[settings.application] == "e-Callisto FITS Analyzer.app"
    assert settings.symlinks == {"Applications": "/Applications"}
    assert "e-Callisto FITS Analyzer.app" in settings.icon_locations
    assert "Applications" in settings.icon_locations
