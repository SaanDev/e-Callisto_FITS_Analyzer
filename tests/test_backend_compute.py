"""
e-CALLISTO FITS Analyzer
Version 2.8.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import builtins

import numpy as np
import pytest

from src.Backend import compute


@pytest.fixture(autouse=True)
def _reset_backend():
    compute.reset_for_tests()
    yield
    compute.reset_for_tests()


def _fake_device(platform: str, kind: str, memory: int | None = None):
    class _Device:
        def __init__(self):
            self.platform = platform
            self.device_kind = kind

        def memory_stats(self):
            if memory is None:
                raise RuntimeError("no stats")
            return {"bytes_limit": memory}

    return _Device()


def test_numpy_device_is_always_offered():
    devices = compute.available_devices()
    assert devices[-1].backend == compute.BACKEND_NUMPY
    assert compute.to_numpy(np.arange(3)).dtype is not None


def test_falls_back_to_numpy_when_jax_is_missing(monkeypatch):
    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name == "jax" or name.startswith("jax."):
            raise ImportError("jax is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    compute.reset_for_tests()

    assert compute.available_devices() == [compute.NUMPY_DEVICE]
    assert compute.active_device() == compute.NUMPY_DEVICE
    assert compute.is_accelerated() is False
    assert compute.is_gpu() is False
    assert compute.jax_version() is None
    assert "jax is not installed" in (compute.jax_import_error() or "")
    # numpy_module must still hand back something usable.
    assert compute.numpy_module() is np


def test_gpu_device_is_detected_and_preferred(monkeypatch):
    monkeypatch.setattr(
        compute,
        "_import_jax",
        lambda: (type("_Jax", (), {"devices": staticmethod(lambda: [
            _fake_device("cpu", "cpu"),
            _fake_device("gpu", "NVIDIA RTX 4070", 12 * 1024**3),
        ])}), None),
    )
    compute.reset_for_tests()
    monkeypatch.setattr(
        compute,
        "_import_jax",
        lambda: (type("_Jax", (), {"devices": staticmethod(lambda: [
            _fake_device("cpu", "cpu"),
            _fake_device("gpu", "NVIDIA RTX 4070", 12 * 1024**3),
        ])}), None),
    )

    devices = compute.available_devices(refresh=True)
    backends = [d.backend for d in devices]
    assert compute.BACKEND_CUDA in backends
    assert compute.BACKEND_CPU in backends

    # Auto must prefer the GPU over the CPU backend.
    active = compute.select_backend(compute.BACKEND_AUTO)
    assert active.backend == compute.BACKEND_CUDA
    assert active.memory_bytes == 12 * 1024**3
    assert compute.is_gpu() is True
    assert "RTX 4070" in compute.describe_active()


def test_broken_device_enumeration_degrades_to_numpy(monkeypatch):
    def _boom():
        raise RuntimeError("CUDA driver version is insufficient")

    monkeypatch.setattr(
        compute,
        "_import_jax",
        lambda: (type("_Jax", (), {"devices": staticmethod(_boom)}), None),
    )
    compute.reset_for_tests()

    devices = compute.available_devices(refresh=True)
    assert devices == [compute.NUMPY_DEVICE]
    assert compute.active_device() == compute.NUMPY_DEVICE


def test_selecting_an_unavailable_backend_falls_back(monkeypatch):
    monkeypatch.setattr(compute, "_import_jax", lambda: (None, None))
    compute.reset_for_tests()

    device = compute.select_backend(compute.BACKEND_CUDA)
    assert device == compute.NUMPY_DEVICE
    assert compute.requested_backend() == compute.BACKEND_CUDA


def test_unknown_backend_name_is_rejected():
    with pytest.raises(ValueError):
        compute.select_backend("opencl")


def test_metal_is_not_probed_without_opt_in(monkeypatch):
    monkeypatch.delenv(compute.METAL_OPT_IN_ENV, raising=False)
    compute.reset_for_tests()
    assert compute._probe_metal() is None


def test_metal_probe_rejects_a_backend_that_cannot_compute(monkeypatch):
    monkeypatch.setenv(compute.METAL_OPT_IN_ENV, "1")

    class _Jax:
        @staticmethod
        def devices():
            return [_fake_device("metal", "Apple M1")]

    class _Jnp:
        @staticmethod
        def asarray(*args, **kwargs):
            raise RuntimeError("PJRT plugin API version mismatch")

    monkeypatch.setattr(compute, "_import_jax", lambda: (_Jax, _Jnp))
    compute.reset_for_tests()
    monkeypatch.setenv(compute.METAL_OPT_IN_ENV, "1")

    assert compute._probe_metal() is None


def test_should_accelerate_respects_size_and_gpu_only(monkeypatch):
    gpu = compute.DeviceInfo(compute.BACKEND_CUDA, "gpu", "NVIDIA", None, False)
    cpu = compute.DeviceInfo(compute.BACKEND_CPU, "cpu", "CPU", None, False)

    big = np.zeros((1000, 1000), dtype=np.float32)
    small = np.zeros((4, 4), dtype=np.float32)

    monkeypatch.setattr(compute, "active_device", lambda: gpu)
    assert compute.should_accelerate(big) is True
    assert compute.should_accelerate(small) is False
    assert compute.should_accelerate(big, gpu_only=True) is True

    monkeypatch.setattr(compute, "active_device", lambda: cpu)
    assert compute.should_accelerate(big) is True
    # gpu_only kernels stay on NumPy when only a CPU JAX backend is present.
    assert compute.should_accelerate(big, gpu_only=True) is False

    monkeypatch.setattr(compute, "active_device", lambda: compute.NUMPY_DEVICE)
    assert compute.should_accelerate(big) is False


def test_dispatch_falls_back_when_the_jax_path_raises(monkeypatch):
    monkeypatch.setattr(compute, "should_accelerate", lambda *a, **k: True)
    calls = []

    def _jax(value):
        calls.append("jax")
        raise RuntimeError("unsupported op")

    def _numpy(value):
        calls.append("numpy")
        return value * 2

    assert compute.dispatch(_jax, _numpy, 21) == 42
    assert calls == ["jax", "numpy"]


def test_dispatch_uses_numpy_when_not_accelerated(monkeypatch):
    monkeypatch.setattr(compute, "should_accelerate", lambda *a, **k: False)
    sentinel = object()
    assert compute.dispatch(lambda _: pytest.fail("jax path must not run"), lambda _: sentinel, 1) is sentinel


def test_to_numpy_returns_contiguous_ndarray():
    source = np.arange(12, dtype=np.float32).reshape(3, 4)
    out = compute.to_numpy(source[:, ::2])
    assert isinstance(out, np.ndarray)
    assert out.flags["C_CONTIGUOUS"]
    assert np.array_equal(out, source[:, ::2])


def test_to_numpy_result_is_writable():
    """JAX hands back read-only buffers; callers write into the result."""
    source = np.arange(6, dtype=np.float32)
    source.flags.writeable = False

    out = compute.to_numpy(source)
    assert out.flags["WRITEABLE"]
    out[0] = 99.0
    assert source[0] == 0.0


def test_to_numpy_passes_none_through():
    assert compute.to_numpy(None) is None


def test_jit_kernel_runs_without_jax(monkeypatch):
    monkeypatch.setattr(compute, "_import_jax", lambda: (None, None))
    compute.reset_for_tests()

    wrapped = compute.jit_kernel(lambda a, b: a + b)
    assert wrapped(2, 3) == 5
    assert wrapped.uncompiled(2, 3) == 5


def test_warmup_is_a_noop_without_acceleration(monkeypatch):
    monkeypatch.setattr(compute, "is_accelerated", lambda: False)
    compute.warmup([lambda: pytest.fail("warmup must not run on NumPy")])


def test_warmup_swallows_kernel_failures(monkeypatch):
    monkeypatch.setattr(compute, "is_accelerated", lambda: True)
    ran = []

    def _boom():
        ran.append(True)
        raise RuntimeError("compile failed")

    compute.warmup([_boom])
    assert ran == [True]


def test_prewarm_runs_only_once_per_process(monkeypatch):
    calls = []
    monkeypatch.setattr(compute, "active_device", lambda: calls.append(1))

    compute.prewarm()
    compute.prewarm()
    compute.prewarm()
    assert len(calls) == 1


def test_is_probed_reflects_discovery():
    assert compute.is_probed() is False
    compute.available_devices()
    assert compute.is_probed() is True


def test_set_requested_backend_does_not_probe():
    assert compute.set_requested_backend(compute.BACKEND_CUDA) == compute.BACKEND_CUDA
    assert compute.requested_backend() == compute.BACKEND_CUDA
    assert compute.is_probed() is False


def test_set_requested_backend_normalises_junk():
    assert compute.set_requested_backend("nonsense") == compute.BACKEND_AUTO
    assert compute.set_requested_backend("") == compute.BACKEND_AUTO
    assert compute.set_requested_backend(None) == compute.BACKEND_AUTO
