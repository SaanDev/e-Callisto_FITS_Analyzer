"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import os
import sys


def _canonical_path(path: str) -> str:
    return os.path.realpath(os.path.abspath(path))


def project_base_path(
    *,
    module_file: str,
    executable: str | None = None,
    frozen: bool | None = None,
    meipass: str | None = None,
    platform_name: str | None = None,
) -> str:
    executable = executable if executable is not None else sys.executable
    frozen = bool(getattr(sys, "frozen", False) if frozen is None else frozen)
    meipass = getattr(sys, "_MEIPASS", "") if meipass is None else meipass
    platform_name = sys.platform if platform_name is None else platform_name

    if frozen:
        if meipass:
            return _canonical_path(meipass)

        exe_dir = _canonical_path(os.path.dirname(executable))
        if platform_name == "darwin":
            return _canonical_path(os.path.join(exe_dir, "..", "Resources"))
        return exe_dir

    return _canonical_path(os.path.join(os.path.dirname(module_file), "..", ".."))


def runtime_base_paths(
    *,
    base_path: str = "",
    executable: str | None = None,
    meipass: str | None = None,
) -> list[str]:
    executable = executable if executable is not None else sys.executable
    meipass = getattr(sys, "_MEIPASS", "") if meipass is None else meipass

    candidates = [
        base_path,
        os.path.dirname(executable) if executable else "",
        meipass,
    ]

    results: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        normalized = os.path.normcase(_canonical_path(candidate))
        if normalized in seen:
            continue
        seen.add(normalized)
        results.append(_canonical_path(candidate))
    return results


def find_startup_logo_path(
    *,
    base_path: str = "",
    executable: str | None = None,
    meipass: str | None = None,
) -> str:
    relative_candidates = [
        os.path.join("assets", "FITS_analyzer.png"),
        os.path.join("assets", "icons", "FITS_analyzer.png"),
        "FITS_analyzer.png",
    ]

    for root in runtime_base_paths(base_path=base_path, executable=executable, meipass=meipass):
        for relative_path in relative_candidates:
            candidate = os.path.join(root, relative_path)
            if os.path.exists(candidate):
                return candidate
    return ""
