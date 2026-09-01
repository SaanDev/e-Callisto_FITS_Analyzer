"""
e-CALLISTO FITS Analyzer
Version 2.8.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Re-sign every Mach-O binary inside a macOS .app bundle, inside-out.

py2app rewrites Mach-O load commands (via macholib) to point at the bundled
copies of each library. That edit invalidates whatever signature the library
shipped with. `codesign --deep` does not repair them, because --deep only walks
*bundle* code locations (Contents/MacOS, Contents/Frameworks, PlugIns); py2app
puts PySide6's Qt frameworks under Contents/Resources/lib/pythonX.Y/, which is
a resource location, so codesign never descends into it.

The result is an app that launches until it loads one of those libraries, at
which point AMFI kills the process with SIGKILL (Code Signature Invalid).
QtWebEngineCore is the usual casualty.

This script signs every Mach-O file first, then nested .app/.framework bundles
deepest-first, then the outer bundle last, so each signature seals content that
is already final.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import platform
import struct
import subprocess
import sys
from pathlib import Path

from macholib.MachO import MachO
from macholib.mach_o import LC_BUILD_VERSION, LC_VERSION_MIN_MACOSX

MACHO_MAGIC = {
    0xFEEDFACE, 0xFEEDFACF,  # 32/64-bit, native order
    0xCEFAEDFE, 0xCFFAEDFE,  # byte-swapped
    0xCAFEBABE, 0xBEBAFECA,  # universal (fat)
}


def is_macho(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            head = handle.read(4)
    except OSError:
        return False
    if len(head) < 4:
        return False
    return struct.unpack(">I", head)[0] in MACHO_MAGIC


def sign(target: Path, identity: str) -> tuple[Path, bool, str]:
    # py2app/macholib may leave a now-invalid signature after rewriting load
    # commands. Remove that stale signature first so codesign creates a fresh
    # code directory and, for bundles, a fresh resource seal.
    subprocess.run(
        ["codesign", "--remove-signature", str(target)],
        capture_output=True,
        text=True,
    )

    result = subprocess.run(
        ["codesign", "--force", "--sign", identity, "--timestamp=none", str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return target, False, (result.stderr or result.stdout or "").strip()

    verification = subprocess.run(
        ["codesign", "--verify", "--strict", "--verbose=2", str(target)],
        capture_output=True,
        text=True,
    )
    return (
        target,
        verification.returncode == 0,
        (verification.stderr or verification.stdout or "").strip(),
    )


def depth(path: Path) -> int:
    return len(path.parts)


def version_tuple(value: str) -> tuple[int, int, int]:
    parts: list[int] = []
    for component in value.split("."):
        digits = "".join(character for character in component if character.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    padded = (parts + [0, 0, 0])[:3]
    return padded[0], padded[1], padded[2]


def decode_macho_version(value: int) -> tuple[int, int, int]:
    return (value >> 16, (value >> 8) & 0xFF, value & 0xFF)


def minimum_macos_versions(path: Path) -> set[tuple[int, int, int]]:
    versions: set[tuple[int, int, int]] = set()
    try:
        headers = MachO(str(path)).headers
    except (OSError, ValueError, struct.error):
        return versions

    for header in headers:
        for load_command, command, _data in header.commands:
            if load_command.cmd == LC_BUILD_VERSION and getattr(command, "platform", None) == 1:
                versions.add(decode_macho_version(command.minos))
            elif load_command.cmd == LC_VERSION_MIN_MACOSX:
                versions.add(decode_macho_version(command.version))
    return versions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", help="Path to the .app bundle")
    parser.add_argument("--identity", default="-", help="Signing identity ('-' for ad-hoc)")
    parser.add_argument("--jobs", type=int, default=8, help="Parallel codesign workers")
    parser.add_argument(
        "--target-macos",
        default=platform.mac_ver()[0] or "13.0",
        help="Reject bundled binaries that require a newer macOS version",
    )
    args = parser.parse_args()

    bundle = Path(args.bundle).resolve()
    if not bundle.is_dir():
        print(f"Not a bundle: {bundle}", file=sys.stderr)
        return 2

    # Walk without following symlinked directories. Qt frameworks carry
    # Versions/Current -> A (and QtFoo -> Versions/Current/QtFoo), so a naive
    # recursive glob reaches the same physical binary by several paths. Signing
    # one file from two workers at once makes codesign replace the inode under
    # its own feet and fail with "No such file or directory", so collect each
    # real file exactly once.
    machos: list[Path] = []
    nested: list[Path] = []
    seen: set[Path] = set()
    incompatible: list[tuple[Path, tuple[int, int, int]]] = []
    target_macos = version_tuple(args.target_macos)

    for root, dirs, files in os.walk(bundle, followlinks=False):
        root_path = Path(root)
        dirs[:] = [d for d in dirs if not (root_path / d).is_symlink()]

        for name in dirs:
            path = root_path / name
            if path.suffix in (".app", ".framework") and path != bundle:
                nested.append(path)

        for name in files:
            path = root_path / name
            if path.is_symlink():
                continue
            # Skip bundle main executables. Handing codesign a path inside a
            # bundle's Contents/MacOS makes it sign the *enclosing bundle*
            # rather than that one file, which would run concurrently with the
            # workers signing the bundle's other contents. The bundle phases
            # below cover these binaries anyway.
            bundle_main_executable = (
                path.parent.name == "MacOS" and path.parent.parent.name == "Contents"
            )
            try:
                real = path.resolve()
            except OSError:
                continue
            if real in seen:
                continue
            if is_macho(path):
                seen.add(real)
                for minimum in minimum_macos_versions(path):
                    if minimum > target_macos:
                        incompatible.append((path, minimum))
                if not bundle_main_executable:
                    machos.append(path)

    if incompatible:
        print(
            f"    {len(incompatible)} binary slice(s) require a macOS version newer "
            f"than {args.target_macos}:",
            file=sys.stderr,
        )
        for target, minimum in incompatible[:30]:
            required = ".".join(str(part) for part in minimum)
            print(
                f"      {target.relative_to(bundle)} requires macOS {required}",
                file=sys.stderr,
            )
        print(
            "    Reinstall the named dependency from a macOS-compatible wheel or build it "
            "from source with MACOSX_DEPLOYMENT_TARGET set.",
            file=sys.stderr,
        )
        return 1

    print(
        f"    inside-out signer: {len(machos)} Mach-O files, "
        f"{len(nested)} nested bundles",
        flush=True,
    )

    failures: list[tuple[Path, str]] = []

    # Phase 1: individual Mach-O files. Order does not matter here, so run them
    # in parallel; there are hundreds and each codesign call is slow to start.
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for target, ok, err in pool.map(lambda p: sign(p, args.identity), machos):
            if not ok:
                failures.append((target, err))

    # Phase 2: nested bundles, deepest first, so an inner bundle is final before
    # the bundle enclosing it gets sealed.
    for target in sorted(nested, key=depth, reverse=True):
        _, ok, err = sign(target, args.identity)
        if not ok:
            failures.append((target, err))

    # Phase 3: the outer bundle last.
    _, ok, err = sign(bundle, args.identity)
    if not ok:
        failures.append((bundle, err))

    if failures:
        print(f"    {len(failures)} signing failure(s):", file=sys.stderr)
        for target, err in failures[:20]:
            print(f"      {target.relative_to(bundle.parent)}: {err}", file=sys.stderr)
        return 1

    # Check the complete nested-code graph as a final guard against leaving a
    # valid outer seal around an invalid dylib or framework.
    verification = subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(bundle)],
        capture_output=True,
        text=True,
    )
    if verification.returncode != 0:
        print("    final bundle verification failed:", file=sys.stderr)
        print(
            f"      {(verification.stderr or verification.stdout or '').strip()}",
            file=sys.stderr,
        )
        return 1

    print("    all binaries signed and verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
