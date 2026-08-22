#!/usr/bin/env python3
"""
e-CALLISTO FITS Analyzer
Version 2.8.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Benchmark the accelerated compute paths across backends.

    python3 scripts/benchmark_compute.py
    python3 scripts/benchmark_compute.py --backend numpy --backend jax-cpu
    python3 scripts/benchmark_compute.py --shape 4096x4096

Reports wall time per operation for each backend so a change can be judged on
measurements rather than expectations. A kernel that does not beat NumPy should
stay routed to NumPy — see ``gpu_only`` in ``src/Backend/compute.py``.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

warnings.filterwarnings("ignore")

from src.Backend import compute  # noqa: E402

DEFAULT_SHAPES = [(200, 3600), (400, 7200), (2048, 4096)]


def _time(fn, repeats: int = 5) -> float:
    """Best-of-N wall time in milliseconds, after one warmup call."""
    compute.block_until_ready(fn())
    best = float("inf")
    for _ in range(repeats):
        start = time.perf_counter()
        result = fn()
        compute.block_until_ready(result)
        best = min(best, time.perf_counter() - start)
    return best * 1000.0


def _operations(shape):
    import matplotlib

    from src.Backend.coronagraph import nrgf, radial_distance_grid
    from src.Backend.noise_reduction import subtract_background_rows
    from src.Backend.rfi_filters import clean_rfi
    from src.Backend.spectral_overview import _peak_preserving_downsample
    from src.UI.accelerated_plot_widget import _rgba_image_from_cmap

    rng = np.random.default_rng(20260822)
    data = (rng.normal(size=shape) * 3.0 + 10.0).astype(np.float32)
    data[shape[0] // 4, :] = np.nan
    gap = np.zeros(shape[0], dtype=bool)
    gap[shape[0] // 4] = True
    times = np.arange(shape[1], dtype=float)
    cmap = matplotlib.colormaps["viridis"]
    square = min(shape[0], 1024), min(shape[1], 1024)
    image = rng.random(square) * 100.0

    return [
        ("background subtract", lambda: subtract_background_rows(
            data, method="robust", gap_row_mask=gap, equalize_noise=True)),
        ("RFI clean", lambda: clean_rfi(data)),
        ("RGBA colormap", lambda: _rgba_image_from_cmap(
            data, cmap, vmin=0.0, vmax=20.0, gap_row_mask=gap)),
        ("peak downsample", lambda: _peak_preserving_downsample(
            data, times, max_columns=max(1, shape[1] // 4))),
        ("radial distance grid", lambda: radial_distance_grid(square, (square[1] / 2, square[0] / 2))),
        ("NRGF", lambda: nrgf(image, (square[1] / 2, square[0] / 2))),
    ]


def _parse_shape(text: str) -> tuple[int, int]:
    rows, _, cols = text.lower().partition("x")
    return int(rows), int(cols)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--backend",
        action="append",
        choices=list(compute.BACKEND_CHOICES),
        help="Backend to measure; repeat for several. Defaults to every backend this machine has.",
    )
    parser.add_argument("--shape", action="append", help="Array shape as ROWSxCOLS, e.g. 200x3600.")
    parser.add_argument("--repeats", type=int, default=5, help="Timed runs per operation (default 5).")
    args = parser.parse_args()

    shapes = [_parse_shape(s) for s in args.shape] if args.shape else DEFAULT_SHAPES
    available = {device.backend for device in compute.available_devices()}
    backends = args.backend or [b for b in compute.BACKEND_CHOICES if b in available or b == compute.BACKEND_NUMPY]

    print("e-CALLISTO compute benchmark")
    print(f"  devices: {', '.join(d.label for d in compute.available_devices())}")
    print(f"  JAX:     {compute.jax_version() or 'not installed'}")
    print()

    for shape in shapes:
        print(f"=== {shape[0]}x{shape[1]} ===")
        results: dict[str, dict[str, float]] = {}
        for backend in backends:
            device = compute.select_backend(backend)
            if backend not in (compute.BACKEND_AUTO, compute.BACKEND_NUMPY) and device.backend != backend:
                print(f"  (skipping {backend}: not available here)")
                continue
            results[backend] = {
                name: _time(fn, args.repeats) for name, fn in _operations(shape)
            }

        names = [name for name, _ in _operations(shape)]
        header = f"  {'operation':<22}" + "".join(f"{b:>14}" for b in results)
        print(header)
        print("  " + "-" * (len(header) - 2))
        for name in names:
            row = f"  {name:<22}"
            for backend in results:
                row += f"{results[backend][name]:>11.1f} ms"
            print(row)
        print()

    if not any(device.is_gpu for device in compute.available_devices()):
        print(
            "Note: the sort-bound and bandwidth-bound kernels are marked gpu_only, so\n"
            "      without a GPU they run the same NumPy code on every backend and the\n"
            "      columns are expected to match. Run this on a CUDA machine to see the\n"
            "      JAX paths diverge."
        )

    compute.select_backend(compute.BACKEND_AUTO)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
