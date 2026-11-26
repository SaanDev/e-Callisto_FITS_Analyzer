#!/usr/bin/env bash
# Build the Linux ARM64 .deb of e-CALLISTO FITS Analyzer Lite. The full app
# has its own script: packaging/linux/build_deb_linux_arm64.sh.
#
# Output:
#   lite/dist/e-callisto-fits-analyzer_<version>-lite_arm64.deb
#
# Usage, on Ubuntu arm64 (22.04 or 24.04), from anywhere:
#   bash lite/packaging/linux/build_deb_linux_arm64.sh
# Optional:
#   INSTALL_DEPS=0   do not apt-get the build prerequisites
#   MAX_GLIBC=2.35   fail unless the package runs on this glibc (22.04: 2.35, 24.04: 2.39)
#   VERSION=...      the package version (default: Lite's Cargo version, 3.3.0-lite)
#
# What it needs: Rust from https://rustup.rs. Everything else comes from apt.
#
# Lite is one Rust binary with no Qt, so unlike the full app it is not tied
# to Ubuntu 24.04: the package needs the glibc of the machine it is built on
# (printed at the end), so build on 22.04 for a package that installs there.
# It must be built on arm64: lite/packaging/linux/build_deb_linux.sh runs a
# helper (export_icons) it builds, and names the package for this machine.
set -euo pipefail

LITE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INSTALL_DEPS="${INSTALL_DEPS:-1}"
MAX_GLIBC="${MAX_GLIBC:-}"
VERSION="${VERSION:-$(awk -F'"' '/^version[[:space:]]*=/{print $2; exit}' "$LITE/Cargo.toml")}"
export VERSION
DEB="$LITE/dist/e-callisto-fits-analyzer_${VERSION}_arm64.deb"
BIN="$LITE/target/release/callisto-lite"

step() { printf '\n==> %s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
version_gt() { [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n1)" = "$1" ] && [ "$1" != "$2" ]; }

# --- Preflight ---------------------------------------------------------------
command -v dpkg >/dev/null 2>&1 || die "dpkg not found: this script is for Debian/Ubuntu."
ARCH="$(dpkg --print-architecture)"
[ "$ARCH" = "arm64" ] || die "This machine is $ARCH; build Lite's arm64 package on Ubuntu arm64."
HOST_GLIBC="$(getconf GNU_LIBC_VERSION | awk '{print $2}')"
OS_NAME="$( . /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}")"
step "e-CALLISTO FITS Analyzer Lite $VERSION for Linux arm64"
echo "Host: $OS_NAME, glibc $HOST_GLIBC"

if [ "$INSTALL_DEPS" = 1 ]; then
  step "Installing build prerequisites with apt (sudo)"
  sudo apt-get update
  # The window toolkit's headers, as .github/workflows/lite.yml installs them.
  sudo apt-get install -y build-essential pkg-config binutils file dpkg-dev curl ca-certificates \
    libxkbcommon-dev libxcb-render0-dev libxcb-shape0-dev libxcb-xfixes0-dev
fi

if ! command -v cargo >/dev/null 2>&1 && [ -x "$HOME/.cargo/bin/cargo" ]; then
  export PATH="$HOME/.cargo/bin:$PATH"   # rustup installed, shell not reopened yet
fi
command -v cargo >/dev/null 2>&1 \
  || die "Rust (cargo) not found. Install it from https://rustup.rs, open a new shell, and run this again."
echo "Rust: $(rustc --version)"

# --- Build ---------------------------------------------------------------------
step "cargo and .deb (lite/packaging/linux/build_deb_linux.sh)"
# The release binary lands in lite/target unless CARGO_TARGET_DIR says otherwise.
[ -n "${CARGO_TARGET_DIR:-}" ] && BIN="$CARGO_TARGET_DIR/release/callisto-lite"
bash "$LITE/packaging/linux/build_deb_linux.sh"
[ -f "$DEB" ] || die "The build finished, but $DEB is missing."

# --- Checks --------------------------------------------------------------------
step "Checking the package"
[ "$(dpkg-deb -f "$DEB" Architecture)" = "arm64" ] || die "$DEB is not marked arm64."
file -b "$BIN" | grep -q "ARM aarch64" || die "$BIN is not an ARM64 executable: $(file -b "$BIN")"
NEEDS="$(objdump -T "$BIN" 2>/dev/null | grep -oE 'GLIBC_2\.[0-9]+' | sed 's/GLIBC_//' | sort -V | tail -n1 || true)"
echo "Executable: ARM64; needs glibc ${NEEDS:-unknown} or newer"
if [ -n "$MAX_GLIBC" ] && [ -n "$NEEDS" ] && version_gt "$NEEDS" "$MAX_GLIBC"; then
  die "Lite needs glibc $NEEDS, newer than MAX_GLIBC=$MAX_GLIBC: build it on an older Ubuntu."
fi

step "Done"
echo "Lite: $DEB"
echo "Install: sudo apt install -y \"$DEB\""
