"""Restore valid OpenSSL Mach-O files after py2app dependency rewriting."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath

OPENSSL_LIBRARIES = ("libssl.3.dylib", "libcrypto.3.dylib")


def _run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise RuntimeError(f"{' '.join(command)} failed:\n{detail}")
    return result.stdout


def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for component in value.split("."):
        digits = "".join(character for character in component if character.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple((parts + [0, 0, 0])[:3])


def _minimum_macos_version(path: Path) -> tuple[int, ...] | None:
    lines = _run(["otool", "-l", str(path)]).splitlines()
    for index, line in enumerate(lines):
        command = line.strip()
        block = [item.strip() for item in lines[index + 1 : index + 10]]
        if command == "cmd LC_BUILD_VERSION":
            platform_line = next((item for item in block if item.startswith("platform ")), "")
            if platform_line != "platform 1":
                continue
            version_line = next((item for item in block if item.startswith("minos ")), "")
            if version_line:
                return _version_tuple(version_line.split(maxsplit=1)[1])
        elif command == "cmd LC_VERSION_MIN_MACOSX":
            version_line = next((item for item in block if item.startswith("version ")), "")
            if version_line:
                return _version_tuple(version_line.split(maxsplit=1)[1])
    return None


def _candidate_directories(explicit: str) -> list[Path]:
    candidates: list[Path] = []
    if explicit:
        supplied = Path(explicit).expanduser()
        candidates.extend((supplied, supplied / "lib"))

    for variable in ("OPENSSL_LIB_DIR", "OPENSSL_DIR", "OPENSSL_ROOT_DIR"):
        value = os.environ.get(variable, "")
        if value:
            supplied = Path(value).expanduser()
            candidates.extend((supplied, supplied / "lib"))

    candidates.extend(
        (
            Path("/opt/homebrew/opt/openssl@3/lib"),
            Path("/usr/local/opt/openssl@3/lib"),
            Path("/opt/local/lib"),
        )
    )

    unique: list[Path] = []
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate not in unique:
            unique.append(candidate)
    return unique


def _find_source_directory(explicit: str) -> Path:
    for directory in _candidate_directories(explicit):
        if all((directory / name).is_file() for name in OPENSSL_LIBRARIES):
            return directory
    searched = "\n  ".join(str(path) for path in _candidate_directories(explicit))
    raise RuntimeError(
        "Could not find pristine OpenSSL 3 libraries. Searched:\n"
        f"  {searched}\n"
        "Install openssl@3 or pass --openssl-lib-dir /path/to/lib."
    )


def _dependencies(path: Path) -> list[str]:
    lines = _run(["otool", "-L", str(path)]).splitlines()[1:]
    dependencies: list[str] = []
    for line in lines:
        dependency = line.strip().split(" (compatibility version", 1)[0]
        if dependency:
            dependencies.append(dependency)
    return dependencies


def repair(app_bundle: Path, source_directory: Path, target_macos: str) -> None:
    frameworks = app_bundle / "Contents" / "Frameworks"
    destinations = {name: frameworks / name for name in OPENSSL_LIBRARIES}
    missing = [str(path) for path in destinations.values() if not path.exists()]
    if missing:
        raise RuntimeError("The app is missing bundled OpenSSL libraries:\n  " + "\n  ".join(missing))

    target_version = _version_tuple(target_macos)
    for name in OPENSSL_LIBRARIES:
        source = source_directory / name
        minimum = _minimum_macos_version(source)
        if minimum is not None and minimum > target_version:
            found = ".".join(str(part) for part in minimum)
            raise RuntimeError(
                f"{source} requires macOS {found}, but this build targets macOS {target_macos}. "
                "Reinstall openssl@3 from source on the target macOS version."
            )

    for name, destination in destinations.items():
        destination.chmod(destination.stat().st_mode | stat.S_IWUSR)
        shutil.copy2(source_directory / name, destination)
        destination.chmod(destination.stat().st_mode | stat.S_IWUSR)

    for name, destination in destinations.items():
        _run(["install_name_tool", "-id", f"@rpath/{name}", str(destination)])
        for dependency in _dependencies(destination):
            dependency_name = PurePosixPath(dependency).name
            if dependency_name in OPENSSL_LIBRARIES and dependency_name != name:
                _run(
                    [
                        "install_name_tool",
                        "-change",
                        dependency,
                        f"@loader_path/{dependency_name}",
                        str(destination),
                    ]
                )

    ssl_dependencies = _dependencies(destinations["libssl.3.dylib"])
    if "@loader_path/libcrypto.3.dylib" not in ssl_dependencies:
        raise RuntimeError("The repaired libssl.3.dylib does not reference its bundled libcrypto.3.dylib")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", help="Path to the py2app .app bundle")
    parser.add_argument("--openssl-lib-dir", default="", help="Directory containing pristine OpenSSL 3 dylibs")
    parser.add_argument(
        "--target-macos",
        default=platform.mac_ver()[0] or "13.0",
        help="Oldest supported macOS version",
    )
    args = parser.parse_args()

    app_bundle = Path(args.bundle).expanduser().resolve()
    if not app_bundle.is_dir():
        print(f"Not an app bundle: {app_bundle}", file=sys.stderr)
        return 2

    try:
        source_directory = _find_source_directory(args.openssl_lib_dir)
        print(f"    restoring OpenSSL libraries from {source_directory}")
        repair(app_bundle, source_directory, args.target_macos)
    except RuntimeError as exc:
        print(f"OpenSSL bundle repair failed: {exc}", file=sys.stderr)
        return 1

    print("    OpenSSL Mach-O layout repaired")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
