"""
e-CALLISTO FITS Analyzer
Version 2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME_REQUIREMENTS = HERE / "requirements-runtime.txt"
BUILD_REQUIREMENTS = HERE / "requirements-build.txt"


def _requirement_lines(path: Path) -> list[str]:
    if not path.exists():
        raise SystemExit(f"Missing requirements file: {path}")

    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def _package_name(requirement: str) -> str:
    token = str(requirement or "").strip()
    if not token:
        return ""
    # Keep only the distribution name part (for tests/UI output).
    parts = re.split(r"[<>=!~;\s\[]", token, maxsplit=1)
    return parts[0].strip()


runtime_requirements = _requirement_lines(RUNTIME_REQUIREMENTS)
build_requirements = _requirement_lines(BUILD_REQUIREMENTS)

packages = [_package_name(item) for item in runtime_requirements if _package_name(item)]
build_packages = [_package_name(item) for item in build_requirements if _package_name(item)]


def install_requirements_file(path: Path) -> None:
    print(f"Installing requirements from {path} ...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--requirement", str(path)])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install pinned dependencies for e-CALLISTO FITS Analyzer."
    )
    parser.add_argument(
        "--with-build",
        action="store_true",
        help="Also install build tooling from requirements-build.txt.",
    )
    args = parser.parse_args()

    print("=== Installing runtime requirements for e-CALLISTO FITS Analyzer ===")
    install_requirements_file(RUNTIME_REQUIREMENTS)

    if args.with_build:
        print("\n=== Installing build requirements ===")
        install_requirements_file(BUILD_REQUIREMENTS)

    print("\nInstall complete.")
    print("You can start the application with:\n   python3 src/UI/main.py")


if __name__ == "__main__":
    main()
