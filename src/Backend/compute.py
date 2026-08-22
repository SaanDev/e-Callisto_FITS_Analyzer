"""
e-CALLISTO FITS Analyzer
Version 2.8.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Compute backend abstraction.

This is the only module in the project that imports ``jax``. Everything else asks
it which backend is active, hands it NumPy arrays, and gets NumPy arrays back.

The contract for callers:

  * every public function elsewhere keeps returning ``np.ndarray``;
  * every accelerated code path keeps its NumPy implementation and falls back to
    it when JAX is missing, when the workload is too small to be worth the
    dispatch overhead, or when the JAX path raises for any reason.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

import numpy as np

_logger = logging.getLogger("ecallisto.compute")

BACKEND_AUTO = "auto"
BACKEND_NUMPY = "numpy"
BACKEND_CPU = "jax-cpu"
BACKEND_CUDA = "jax-cuda"
BACKEND_METAL = "jax-metal"

BACKEND_CHOICES = (BACKEND_AUTO, BACKEND_NUMPY, BACKEND_CPU, BACKEND_CUDA, BACKEND_METAL)

BACKEND_LABELS = {
    BACKEND_AUTO: "Auto",
    BACKEND_NUMPY: "NumPy (no JAX)",
    BACKEND_CPU: "CPU",
    BACKEND_CUDA: "NVIDIA GPU",
    BACKEND_METAL: "Apple GPU (experimental)",
}

# Below this element count the JAX dispatch and host/device transfer cost
# outweighs anything XLA saves, so small arrays stay on NumPy even when a GPU is
# present. Measured on 200x3600 CALLISTO spectrograms (720k elements).
MIN_ACCELERATED_ELEMENTS = 200_000

# jax-metal is an out-of-tree PJRT plugin that has not been released since
# 2024-10 and is compiled against a much older jaxlib C API. It is never probed
# unless the user explicitly opts in.
METAL_OPT_IN_ENV = "ECALLISTO_ENABLE_JAX_METAL"

NUMPY_PLATFORM = "cpu"
GPU_PLATFORMS = ("gpu", "cuda", "rocm", "metal")


@dataclass(frozen=True)
class DeviceInfo:
    """A compute backend the app can actually run on."""

    backend: str
    platform: str
    label: str
    memory_bytes: int | None = None
    experimental: bool = False

    @property
    def is_jax(self) -> bool:
        return self.backend != BACKEND_NUMPY

    @property
    def is_gpu(self) -> bool:
        return self.platform in GPU_PLATFORMS


NUMPY_DEVICE = DeviceInfo(
    backend=BACKEND_NUMPY,
    platform=NUMPY_PLATFORM,
    label="NumPy (CPU, single core)",
    memory_bytes=None,
    experimental=False,
)


_state_lock = threading.RLock()
_jax_module: Any = None
_jnp_module: Any = None
_jax_import_attempted = False
_jax_import_error: str | None = None
_devices_cache: list[DeviceInfo] | None = None
_requested_backend: str = BACKEND_AUTO
_active_device: DeviceInfo = NUMPY_DEVICE
_active_resolved = False
_warned_fallbacks: set[str] = set()
_prewarm_started = False


# --------------------------------------------------------------------------
# JAX import
# --------------------------------------------------------------------------


def _import_jax() -> tuple[Any, Any]:
    """Import jax lazily. Returns ``(jax, jax.numpy)`` or ``(None, None)``."""
    global _jax_module, _jnp_module, _jax_import_attempted, _jax_import_error

    with _state_lock:
        if _jax_import_attempted:
            return _jax_module, _jnp_module
        _jax_import_attempted = True

        # float64 has to be enabled before jax.numpy is first imported. Kernels
        # still compute in float32 where the NumPy code already did; this only
        # makes f64 available for axis, header and measurement math.
        os.environ.setdefault("JAX_ENABLE_X64", "1")

        try:
            import jax  # noqa: PLC0415
            import jax.numpy as jnp  # noqa: PLC0415
        except Exception as exc:  # pragma: no cover - depends on the environment
            _jax_import_error = str(exc)
            _logger.info("JAX unavailable, using NumPy: %s", exc)
            return None, None

        _jax_module = jax
        _jnp_module = jnp
        return jax, jnp


def jax_version() -> str | None:
    jax, _ = _import_jax()
    return None if jax is None else str(getattr(jax, "__version__", "unknown"))


def jax_import_error() -> str | None:
    _import_jax()
    return _jax_import_error


def numpy_module() -> Any:
    """``jax.numpy`` when a JAX backend is active, otherwise ``numpy``."""
    if not is_accelerated():
        return np
    _, jnp = _import_jax()
    return jnp if jnp is not None else np


# --------------------------------------------------------------------------
# Device discovery
# --------------------------------------------------------------------------


def _device_memory_bytes(device: Any) -> int | None:
    try:
        stats = device.memory_stats()
    except Exception:
        return None
    if not isinstance(stats, dict):
        return None
    for key in ("bytes_limit", "bytes_reservable_limit", "bytes_capacity"):
        value = stats.get(key)
        if value:
            try:
                return int(value)
            except Exception:
                continue
    return None


def _device_label(device: Any, platform: str) -> str:
    kind = str(getattr(device, "device_kind", "") or "").strip()
    if platform in ("gpu", "cuda"):
        return f"NVIDIA {kind}" if kind and "nvidia" not in kind.lower() else (kind or "NVIDIA GPU")
    if platform == "metal":
        return kind or "Apple GPU"
    if kind:
        return f"CPU ({kind})"
    return "CPU"


def _backend_for_platform(platform: str) -> str:
    if platform in ("gpu", "cuda", "rocm"):
        return BACKEND_CUDA
    if platform == "metal":
        return BACKEND_METAL
    return BACKEND_CPU


def _metal_opt_in() -> bool:
    raw = str(os.environ.get(METAL_OPT_IN_ENV, "") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _probe_metal() -> DeviceInfo | None:
    """Try to bring up jax-metal and prove it can actually compute.

    The plugin loads before it fails, so a smoke computation is the only
    trustworthy check. Any failure means the device is not offered.
    """
    if not _metal_opt_in():
        return None

    jax, jnp = _import_jax()
    if jax is None or jnp is None:
        return None

    try:
        devices = [d for d in jax.devices() if str(getattr(d, "platform", "")).lower() == "metal"]
    except Exception as exc:
        _logger.info("jax-metal probe failed to enumerate devices: %s", exc)
        return None
    if not devices:
        return None

    device = devices[0]
    try:
        sample = jnp.asarray(np.array([[1.0, np.nan, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32))
        median = jnp.nanmedian(sample, axis=1)
        lut = jnp.asarray(np.arange(8, dtype=np.uint8))
        gathered = jnp.take(lut, jnp.asarray(np.array([0, 3, 7])))
        out_median = np.asarray(median)
        out_gather = np.asarray(gathered)
    except Exception as exc:
        _logger.warning("jax-metal is installed but not usable, ignoring it: %s", exc)
        return None

    if not np.allclose(out_median, np.array([2.0, 5.0], dtype=np.float32), equal_nan=True):
        _logger.warning("jax-metal returned wrong results for nanmedian, ignoring it.")
        return None
    if not np.array_equal(out_gather, np.array([0, 3, 7], dtype=np.uint8)):
        _logger.warning("jax-metal returned wrong results for take, ignoring it.")
        return None

    return DeviceInfo(
        backend=BACKEND_METAL,
        platform="metal",
        label=_device_label(device, "metal"),
        memory_bytes=_device_memory_bytes(device),
        experimental=True,
    )


def available_devices(*, refresh: bool = False) -> list[DeviceInfo]:
    """Backends this machine can actually run, NumPy always last."""
    global _devices_cache

    with _state_lock:
        if _devices_cache is not None and not refresh:
            return list(_devices_cache)

        found: list[DeviceInfo] = []
        jax, _ = _import_jax()
        if jax is not None:
            try:
                devices = list(jax.devices())
            except Exception as exc:  # pragma: no cover - broken CUDA install
                _logger.warning("JAX device enumeration failed, using NumPy: %s", exc)
                devices = []

            seen: set[str] = set()
            for device in devices:
                platform = str(getattr(device, "platform", "") or "").lower()
                if platform == "metal":
                    # Handled by the opt-in probe below.
                    continue
                backend = _backend_for_platform(platform)
                if backend in seen:
                    continue
                seen.add(backend)
                found.append(
                    DeviceInfo(
                        backend=backend,
                        platform=platform or NUMPY_PLATFORM,
                        label=_device_label(device, platform),
                        memory_bytes=_device_memory_bytes(device),
                        experimental=False,
                    )
                )

            metal = _probe_metal()
            if metal is not None:
                found.append(metal)

        found.append(NUMPY_DEVICE)
        _devices_cache = found
        return list(found)


def _resolve_active(requested: str) -> DeviceInfo:
    devices = available_devices()
    by_backend = {d.backend: d for d in devices}

    name = str(requested or BACKEND_AUTO).strip().lower()
    if name == BACKEND_AUTO:
        for backend in (BACKEND_CUDA, BACKEND_METAL, BACKEND_CPU):
            device = by_backend.get(backend)
            if device is not None and not device.experimental:
                return device
        # Only fall to an experimental backend if it is all that is on offer.
        for backend in (BACKEND_CUDA, BACKEND_METAL, BACKEND_CPU):
            if backend in by_backend:
                return by_backend[backend]
        return NUMPY_DEVICE

    device = by_backend.get(name)
    if device is not None:
        return device

    _logger.info("Compute backend %r is not available here, falling back to auto.", requested)
    return _resolve_active(BACKEND_AUTO)


def active_device() -> DeviceInfo:
    global _active_device, _active_resolved

    with _state_lock:
        if not _active_resolved:
            _active_device = _resolve_active(_requested_backend)
            _active_resolved = True
        return _active_device


def requested_backend() -> str:
    with _state_lock:
        return _requested_backend


def set_requested_backend(name: str) -> str:
    """Record the wanted backend without probing for devices.

    Importing JAX costs over a second, so startup must not trigger it. The
    actual resolution happens on first use, or when :func:`prewarm` runs it on a
    background thread.
    """
    global _requested_backend, _active_resolved

    normalized = str(name or BACKEND_AUTO).strip().lower()
    if normalized not in BACKEND_CHOICES:
        normalized = BACKEND_AUTO

    with _state_lock:
        _requested_backend = normalized
        _active_resolved = False
        _warned_fallbacks.clear()
    return normalized


def prewarm() -> None:
    """Resolve the active device now. Safe to call from a background thread.

    Runs at most once per process: several windows may ask for it, and importing
    JAX repeatedly would be wasted work.
    """
    global _prewarm_started

    with _state_lock:
        if _prewarm_started or _devices_cache is not None:
            return
        _prewarm_started = True

    try:
        active_device()
    except Exception as exc:  # pragma: no cover - defensive
        _logger.debug("Compute prewarm failed: %s", exc)


def is_probed() -> bool:
    """True once device discovery has actually run."""
    with _state_lock:
        return _devices_cache is not None


def select_backend(name: str) -> DeviceInfo:
    """Choose a backend by name. Returns the device that actually got selected."""
    global _requested_backend, _active_device, _active_resolved

    normalized = str(name or BACKEND_AUTO).strip().lower()
    if normalized not in BACKEND_CHOICES:
        raise ValueError(f"Unknown compute backend: {name!r}")

    with _state_lock:
        _requested_backend = normalized
        _active_device = _resolve_active(normalized)
        _active_resolved = True
        _warned_fallbacks.clear()
        return _active_device


def reset_for_tests() -> None:
    """Drop every cached probe. Tests only."""
    global _jax_module, _jnp_module, _jax_import_attempted, _jax_import_error
    global _devices_cache, _requested_backend, _active_device, _active_resolved
    global _prewarm_started

    with _state_lock:
        _prewarm_started = False
        _jax_module = None
        _jnp_module = None
        _jax_import_attempted = False
        _jax_import_error = None
        _devices_cache = None
        _requested_backend = BACKEND_AUTO
        _active_device = NUMPY_DEVICE
        _active_resolved = False
        _warned_fallbacks.clear()


def is_accelerated() -> bool:
    """True when a JAX backend is active (CPU counts: XLA still fuses)."""
    return active_device().is_jax


def is_gpu() -> bool:
    return active_device().is_gpu


def describe_active() -> str:
    device = active_device()
    parts = [BACKEND_LABELS.get(device.backend, device.backend), device.label]
    if device.memory_bytes:
        parts.append(f"{device.memory_bytes / (1024 ** 3):.1f} GB")
    version = jax_version()
    if version and device.is_jax:
        parts.append(f"JAX {version}")
    return " — ".join(str(p) for p in parts if p)


# --------------------------------------------------------------------------
# Transfers
# --------------------------------------------------------------------------


def to_device(arr: Any, *, dtype: Any = None) -> Any:
    """Move an array onto the active device. Returns NumPy when not accelerated."""
    if not is_accelerated():
        return np.asarray(arr, dtype=dtype) if dtype is not None else np.asarray(arr)

    _, jnp = _import_jax()
    if jnp is None:
        return np.asarray(arr, dtype=dtype) if dtype is not None else np.asarray(arr)
    return jnp.asarray(arr, dtype=dtype)


def to_numpy(arr: Any) -> np.ndarray:
    """Bring an array back to the host as a writable, C-contiguous ``np.ndarray``.

    JAX hands out read-only buffers. Callers treat the result as an ordinary
    array and write into it, so the writability is part of the contract; the
    copy only happens when the source really is read-only.
    """
    if arr is None:
        return None  # type: ignore[return-value]
    out = arr if isinstance(arr, np.ndarray) else np.asarray(arr)
    if not out.flags["WRITEABLE"]:
        return np.ascontiguousarray(np.array(out, copy=True))
    return np.ascontiguousarray(out)


def block_until_ready(arr: Any) -> Any:
    """Force a pending async JAX computation to finish. No-op for NumPy."""
    waiter = getattr(arr, "block_until_ready", None)
    if callable(waiter):
        try:
            return waiter()
        except Exception:
            return arr
    return arr


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------


def _element_count(size_hint: Any) -> int:
    if size_hint is None:
        # No hint means "assume it is worth it" — callers that care pass a shape.
        return MIN_ACCELERATED_ELEMENTS
    if isinstance(size_hint, (int, np.integer)):
        return int(size_hint)
    shape = getattr(size_hint, "shape", None)
    if shape is not None:
        return int(np.prod(shape)) if len(shape) else 1
    if isinstance(size_hint, (tuple, list)):
        try:
            return int(np.prod(size_hint))
        except Exception:
            return 0
    return 0


def should_accelerate(size_hint: Any = None, *, gpu_only: bool = False) -> bool:
    """True when the active backend is JAX and the workload earns the transfer.

    ``gpu_only`` marks a kernel that was measured to be no faster than NumPy on
    JAX's CPU backend — sort-bound row statistics, for instance, where XLA and
    NumPy run the same algorithm. Those stay on NumPy unless a GPU is present.
    """
    device = active_device()
    if not device.is_jax:
        return False
    if gpu_only and not device.is_gpu:
        return False
    return _element_count(size_hint) >= MIN_ACCELERATED_ELEMENTS


def _warn_fallback(name: str, exc: BaseException) -> None:
    with _state_lock:
        if name in _warned_fallbacks:
            return
        _warned_fallbacks.add(name)
    _logger.warning("JAX path for %s failed, using NumPy instead: %s", name, exc)


def dispatch(
    jax_impl: Callable[..., Any],
    numpy_impl: Callable[..., Any],
    *args: Any,
    size_hint: Any = None,
    gpu_only: bool = False,
    **kwargs: Any,
) -> Any:
    """Run ``jax_impl`` when it is worth it, otherwise ``numpy_impl``.

    Both callables must accept the same arguments. Any failure inside the JAX
    path is logged once and falls through to NumPy, so an unsupported op or a
    broken driver degrades instead of crashing.
    """
    if should_accelerate(size_hint, gpu_only=gpu_only):
        try:
            return jax_impl(*args, **kwargs)
        except Exception as exc:
            _warn_fallback(getattr(numpy_impl, "__qualname__", repr(numpy_impl)), exc)
    return numpy_impl(*args, **kwargs)


def jit_kernel(fn: Callable[..., Any], **jit_kwargs: Any) -> Callable[..., Any]:
    """Wrap ``fn`` so it is jitted on first call, if JAX is importable.

    Compilation is deferred because ``jax.jit`` at import time would force the
    JAX import into app startup, and this module has to stay cheap to import.
    """
    import functools

    state: dict[str, Any] = {"compiled": None, "checked": False}

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not state["checked"]:
            state["checked"] = True
            jax, _ = _import_jax()
            if jax is not None:
                try:
                    state["compiled"] = jax.jit(fn, **jit_kwargs)
                except Exception as exc:  # pragma: no cover - bad jit signature
                    _logger.warning("Could not jit %s: %s", getattr(fn, "__name__", fn), exc)
                    state["compiled"] = None
        target = state["compiled"] or fn
        return target(*args, **kwargs)

    wrapper.uncompiled = fn  # type: ignore[attr-defined]
    return wrapper


def warmup(callables: Iterable[Callable[[], Any]]) -> None:
    """Force compilation of the given zero-arg thunks, swallowing failures.

    Call this off the GUI thread after a file loads: XLA's first compile for a
    given input shape costs 100-500 ms, which is otherwise paid on the first
    slider drag.
    """
    if not is_accelerated():
        return
    for thunk in callables:
        try:
            block_until_ready(thunk())
        except Exception as exc:
            _logger.debug("Warmup thunk failed: %s", exc)
