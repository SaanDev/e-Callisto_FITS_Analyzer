"""
Smoke checks for packaged artifacts.

This script validates that:
1) helper mode (`--mode=cme-helper`) starts successfully, and
2) the normal app mode starts as a separate process.
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

KNOWN_MOVIE_URL = "https://cdaw.gsfc.nasa.gov/CME_list/nrl_mpg/2025_11/251103_c2.mpg"


def _default_executable(repo_root: Path) -> Path:
    if sys.platform.startswith("win"):
        return repo_root / "dist" / "e-Callisto FITS Analyzer" / "e-Callisto FITS Analyzer.exe"
    if sys.platform == "darwin":
        return repo_root / "dist" / "e-Callisto FITS Analyzer.app" / "Contents" / "MacOS" / "e-Callisto FITS Analyzer"
    return repo_root / "dist" / "e-callisto-fits-analyzer" / "e-callisto-fits-analyzer"


def _start_and_check(command: list[str], timeout_s: float, startup_grace_s: float = 2.0) -> tuple[bool, str]:
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("QT_OPENGL", "software")

    kwargs = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": env,
    }
    if platform.system() == "Windows":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(command, **kwargs)
    except Exception as exc:
        return False, f"Failed to spawn process: {exc}"

    deadline = time.monotonic() + float(timeout_s)
    started = False
    while time.monotonic() < deadline:
        exit_code = proc.poll()
        if exit_code is None:
            if time.monotonic() + startup_grace_s > deadline:
                started = True
                break
            time.sleep(0.1)
            continue

        return False, f"Process exited early with code {exit_code}"

    if not started and proc.poll() is None:
        started = True

    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    return started, ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Run packaged app smoke tests.")
    parser.add_argument("--exe", default="", help="Path to packaged executable.")
    parser.add_argument("--timeout", type=float, default=8.0, help="Timeout per smoke check in seconds.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    exe_path = Path(args.exe).expanduser().resolve() if args.exe else _default_executable(repo_root)

    if not exe_path.exists():
        print(f"Executable not found: {exe_path}", file=sys.stderr)
        return 2

    helper_cmd = [
        str(exe_path),
        "--mode=cme-helper",
        "--movie-url",
        KNOWN_MOVIE_URL,
        "--movie-title",
        "CME Helper Smoke Test",
    ]
    ok, error = _start_and_check(helper_cmd, timeout_s=args.timeout)
    if not ok:
        print(f"Helper smoke check failed: {error}", file=sys.stderr)
        return 3

    app_cmd = [str(exe_path)]
    ok, error = _start_and_check(app_cmd, timeout_s=args.timeout)
    if not ok:
        print(f"Main app smoke check failed: {error}", file=sys.stderr)
        return 4

    print("Packaged smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

