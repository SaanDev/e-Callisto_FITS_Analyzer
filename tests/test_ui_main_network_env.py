"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


import pytest

pytest.importorskip("PySide6")
pytest.importorskip("certifi")

import certifi

import src.UI.main as main_module


def _clear_ca_env(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)


def test_configure_network_env_is_noop_when_not_frozen(monkeypatch):
    monkeypatch.delattr(main_module.sys, "frozen", raising=False)
    _clear_ca_env(monkeypatch)

    main_module._configure_network_env()

    assert "SSL_CERT_FILE" not in main_module.os.environ
    assert "REQUESTS_CA_BUNDLE" not in main_module.os.environ


def test_configure_network_env_points_to_certifi_when_frozen(monkeypatch):
    monkeypatch.setattr(main_module.sys, "frozen", True, raising=False)
    _clear_ca_env(monkeypatch)

    main_module._configure_network_env()

    assert main_module.os.environ.get("SSL_CERT_FILE") == certifi.where()
    assert main_module.os.environ.get("REQUESTS_CA_BUNDLE") == certifi.where()


def test_configure_network_env_does_not_override_existing(monkeypatch):
    monkeypatch.setattr(main_module.sys, "frozen", True, raising=False)
    _clear_ca_env(monkeypatch)
    monkeypatch.setenv("SSL_CERT_FILE", "/custom/ca.pem")

    main_module._configure_network_env()

    assert main_module.os.environ["SSL_CERT_FILE"] == "/custom/ca.pem"
