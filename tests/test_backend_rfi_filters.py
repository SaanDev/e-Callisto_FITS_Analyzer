"""
e-CALLISTO FITS Analyzer
Version 2.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import numpy as np

from src.Backend.rfi_filters import clean_rfi, config_dict


def test_clean_rfi_is_deterministic():
    rng = np.random.default_rng(123)
    data = rng.normal(0.0, 1.0, size=(16, 32)).astype(np.float32)
    data[5, :] += 14.0

    a = clean_rfi(data, kernel_time=3, kernel_freq=3, channel_z_threshold=4.0, percentile_clip=99.0)
    b = clean_rfi(data, kernel_time=3, kernel_freq=3, channel_z_threshold=4.0, percentile_clip=99.0)

    assert np.array_equal(a.data, b.data)
    assert a.masked_channel_indices == b.masked_channel_indices


def test_clean_rfi_masks_hot_channel():
    data = np.zeros((10, 20), dtype=np.float32)
    data[4, :] = 100.0

    out = clean_rfi(data, channel_z_threshold=2.0)
    assert 4 in out.masked_channel_indices


def test_rfi_config_dict_contract():
    cfg = config_dict(
        enabled=True,
        kernel_time=5,
        kernel_freq=7,
        channel_z_threshold=6.5,
        percentile_clip=99.2,
        masked_channel_indices=[1, 5],
        applied=False,
    )
    assert cfg["enabled"] is True
    assert cfg["kernel_time"] == 5
    assert cfg["kernel_freq"] == 7
    assert cfg["channel_z_threshold"] == 6.5
    assert cfg["percentile_clip"] == 99.2
    assert cfg["masked_channel_indices"] == [1, 5]
    assert cfg["applied"] is False
