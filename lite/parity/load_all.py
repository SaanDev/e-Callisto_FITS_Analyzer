"""Load every FITS file in a folder with the full app's loader; list failures.

Counterpart of ``cargo run -p callisto-core --example load_all -- <folder>``.

usage: python lite/parity/load_all.py <folder>
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.backend.radio.fits_io import load_callisto_fits  # noqa: E402

folder = Path(sys.argv[1])
paths = sorted(p for p in folder.iterdir() if p.name.lower().endswith((".fit", ".fits", ".fit.gz", ".fits.gz")))
start = time.perf_counter()
failed = 0
for p in paths:
    try:
        load_callisto_fits(str(p))
    except Exception as exc:  # noqa: BLE001 - report every failure
        failed += 1
        print(f"FAIL {p.name}: {exc}")
print(f"{len(paths)} files, {failed} failed, {time.perf_counter() - start:.1f} s")
