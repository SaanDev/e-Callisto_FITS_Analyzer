#!/usr/bin/env bash
# Build the Linux ARM64 .deb packages of e-CALLISTO FITS Analyzer and of
# e-CALLISTO FITS Analyzer Lite, for Ubuntu 24.04 (arm64) and newer.
#
# Output:
#   dist/e-callisto-fits-analyzer_<version>_arm64.deb            (full app)
#   lite/dist/e-callisto-fits-analyzer_<version>-lite_arm64.deb  (Lite)
#
# Usage, on Ubuntu 24.04 arm64 (a Raspberry Pi, an ARM server, or a VM on an
# Apple Silicon Mac), from anywhere:
#   bash packaging/linux/build_deb_linux_arm64.sh
# Optional:
#   SKIP_MAIN=1          build Lite only
#   SKIP_LITE=1          build the full app only
#   SMOKE_TEST=0         do not start the built app to check that it opens
#   INSTALL_DEPS=0       do not apt-get the build prerequisites
#   PYTHON_VERSION=3.13  the Python to build with (default 3.12, Ubuntu 24.04's
#                        own; another version comes from the deadsnakes PPA)
#   PYTHON_BIN=/path     a specific python3.12+ (must have its libpython .so)
#   PYSIDE_VERSION=...   default "pinned" (requirements-runtime.txt); see below
#   MAX_GLIBC=2.39       the oldest glibc the packages must run on (default: Ubuntu 24.04's)
#   VERSION=3.3.0        the full app's version (default: src/version.py)
#
# Notes (checked against PyPI 2026-11-26):
#   - PySide6 6.8.1 and later, the pinned 6.11.2 included, publish Linux
#     ARM64 wheels for glibc 2.39 (Ubuntu 24.04) only. That is why this
#     build targets 24.04: the pins are then exactly the x64 build's.
#   - Build on the oldest Ubuntu the package should install on. PyInstaller
#     bundles the build machine's system libraries, so a package built on
#     24.04 needs glibc 2.39 (24.04+), and one built on a newer release needs
#     that release. The script checks every bundled ELF file against
#     MAX_GLIBC at the end.
#   - PyInstaller cannot cross-build: this must run on an arm64 machine.
#   - Ubuntu 22.04 (glibc 2.35) is possible only with
#       PYSIDE_VERSION=6.8.0.2 MAX_GLIBC=2.35 PYTHON_VERSION=3.13
#     (the newest PySide6 with a glibc 2.31 ARM64 wheel; Python from
#     deadsnakes). NOT recommended, measured 2026-11-26 on macOS: every src
#     module imports on 6.8.0.2, but 7 test files that pass on 6.11.2 fail or
#     crash on it (closing the Solar/GCS windows raises "Internal C++ object
#     already deleted" in sunpy_plot_window._enforce_square_map_plot; the
#     analysis sync, SWAVES and multi-station comparison tests segfault).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SKIP_MAIN="${SKIP_MAIN:-0}"
SKIP_LITE="${SKIP_LITE:-0}"
SMOKE_TEST="${SMOKE_TEST:-1}"
INSTALL_DEPS="${INSTALL_DEPS:-1}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
PYSIDE_VERSION="${PYSIDE_VERSION:-pinned}"
MAX_GLIBC="${MAX_GLIBC:-2.39}"
VERSION="${VERSION:-$(awk -F'"' '/^APP_VERSION[[:space:]]*=/{print $2; exit}' "$ROOT/src/version.py")}"
LITE_VERSION="$(awk -F'"' '/^version[[:space:]]*=/{print $2; exit}' "$ROOT/lite/Cargo.toml")"
APP_ID="e-callisto-fits-analyzer"
WORK="$ROOT/build/linux-arm64"
MAIN_DEB="$ROOT/dist/${APP_ID}_${VERSION}_arm64.deb"
LITE_DEB="$ROOT/lite/dist/${APP_ID}_${LITE_VERSION}_arm64.deb"

step() { printf '\n==> %s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
version_gt() { [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n1)" = "$1" ] && [ "$1" != "$2" ]; }

[ "$SKIP_MAIN" = 1 ] && [ "$SKIP_LITE" = 1 ] && die "Nothing to build: SKIP_MAIN=1 and SKIP_LITE=1."

# --- Preflight ---------------------------------------------------------------
command -v dpkg >/dev/null 2>&1 || die "dpkg not found: this script is for Debian/Ubuntu."
ARCH="$(dpkg --print-architecture)"
[ "$ARCH" = "arm64" ] || die "This machine is $ARCH. PyInstaller cannot cross-build: run this on Ubuntu arm64."

HOST_GLIBC="$(getconf GNU_LIBC_VERSION | awk '{print $2}')"
# In a subshell: os-release sets VERSION too.
OS_NAME="$( . /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}")"
step "e-CALLISTO FITS Analyzer $VERSION / Lite $LITE_VERSION for Linux arm64"
echo "Host: $OS_NAME, glibc $HOST_GLIBC; packages must run on glibc $MAX_GLIBC"
if version_gt "$HOST_GLIBC" "$MAX_GLIBC"; then
  cat >&2 <<EOF
Warning: this machine's glibc ($HOST_GLIBC) is newer than MAX_GLIBC ($MAX_GLIBC).
The bundled system libraries will need glibc $HOST_GLIBC, so the packages will
not install on systems with glibc $MAX_GLIBC. Build on the oldest target
release (Ubuntu 24.04 by default), or set MAX_GLIBC=$HOST_GLIBC if newer
systems are the target. The final glibc check will fail otherwise.
EOF
fi
if [ "$PYSIDE_VERSION" = "pinned" ] && version_gt "2.39" "$HOST_GLIBC"; then
  die "This machine has glibc $HOST_GLIBC; the pinned PySide6's ARM64 wheels need 2.39 (Ubuntu 24.04). Upgrade to 24.04, or see the 22.04 note at the top of this script."
fi

if [ "$INSTALL_DEPS" = 1 ]; then
  step "Installing build prerequisites with apt (sudo)"
  sudo apt-get update
  PKGS=(build-essential pkg-config binutils file curl ca-certificates)
  if [ "$SKIP_MAIN" != 1 ] && [ -z "${PYTHON_BIN:-}" ]; then
    if ! apt-cache show "python${PYTHON_VERSION}-dev" >/dev/null 2>&1; then
      echo "python${PYTHON_VERSION} is not in this system's repositories: adding the deadsnakes PPA"
      sudo apt-get install -y software-properties-common
      sudo add-apt-repository -y ppa:deadsnakes/ppa
      sudo apt-get update
    fi
    PKGS+=("python${PYTHON_VERSION}" "python${PYTHON_VERSION}-venv" "python${PYTHON_VERSION}-dev")
  fi
  if [ "$SKIP_MAIN" != 1 ]; then
    # Qt's runtime libraries: PyInstaller loads PySide6 while it analyses the
    # app, the smoke test starts it, and QtWebEngine needs the NSS/X libs.
    PKGS+=(libgl1 libegl1 libfontconfig1 libdbus-1-3 libxkbcommon0 libxkbcommon-x11-0
      libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-image0 libxcb-render-util0 libxcb-randr0
      libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libnss3 libxcomposite1 libxdamage1 libxrandr2
      libxtst6 libxkbfile1)
    # Renamed with the 64-bit time_t transition in 24.04 (libasound2t64, ...).
    for lib in libasound2 libglib2.0-0; do
      if apt-cache show "${lib}t64" >/dev/null 2>&1; then PKGS+=("${lib}t64"); else PKGS+=("$lib"); fi
    done
  fi
  if [ "$SKIP_LITE" != 1 ]; then
    # As .github/workflows/lite.yml installs for the window toolkit.
    PKGS+=(libxkbcommon-dev libxcb-render0-dev libxcb-shape0-dev libxcb-xfixes0-dev dpkg-dev)
  fi
  sudo apt-get install -y "${PKGS[@]}"
fi

if [ "$SKIP_LITE" != 1 ]; then
  command -v cargo >/dev/null 2>&1 || die "Rust (cargo) not found. Install it with rustup (https://rustup.rs), then open a new shell, or set SKIP_LITE=1."
fi

# --- Full app ------------------------------------------------------------------
if [ "$SKIP_MAIN" != 1 ]; then
  if [ -z "${PYTHON_BIN:-}" ]; then
    PYTHON_BIN="$(command -v "python${PYTHON_VERSION}" || true)"
    [ -n "$PYTHON_BIN" ] || die "python${PYTHON_VERSION} not found. Rerun with INSTALL_DEPS=1, or set PYTHON_BIN."
  fi
  "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
    || die "$PYTHON_BIN is older than 3.12 (streamtracer, for PFSS, needs 3.12+)."
  # PyInstaller embeds the shared libpython; Debian ships it in libpython3.X
  # (pulled in by -dev). A static-only build (pyenv's default) cannot be used.
  SONAME="$("$PYTHON_BIN" -c 'import sysconfig; print(sysconfig.get_config_var("INSTSONAME") or "")')"
  LIBDIR="$("$PYTHON_BIN" -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR") or "")')"
  case "$SONAME" in
    *.so*) ;;
    *) die "$PYTHON_BIN has no shared libpython ($SONAME). Use deadsnakes' python${PYTHON_VERSION} or a build with --enable-shared." ;;
  esac
  [ -f "$LIBDIR/$SONAME" ] || ldconfig -p | grep -qF "$SONAME" \
    || die "$SONAME not found: install python${PYTHON_VERSION}-dev (PyInstaller needs the shared libpython)."
  echo "Python: $PYTHON_BIN ($("$PYTHON_BIN" -c 'import platform; print(platform.python_version())'), $SONAME)"

  mkdir -p "$WORK"
  ARM_REQUIREMENTS="$WORK/requirements-runtime-linux-arm64.txt"
  if [ "$PYSIDE_VERSION" = "pinned" ]; then
    cp "$ROOT/requirements/requirements-runtime.txt" "$ARM_REQUIREMENTS"
  else
    sed -E "s/^(PySide6|PySide6-Addons|PySide6-Essentials|shiboken6)==[^[:space:]#]+/\1==${PYSIDE_VERSION}/" \
      "$ROOT/requirements/requirements-runtime.txt" > "$ARM_REQUIREMENTS"
    [ "$(grep -cE "^(PySide6|PySide6-Addons|PySide6-Essentials|shiboken6)==${PYSIDE_VERSION//./\\.}\b" "$ARM_REQUIREMENTS")" = 4 ] \
      || die "Could not set the four PySide6/shiboken6 pins to $PYSIDE_VERSION in $ARM_REQUIREMENTS."
    echo "Pin override: PySide6, PySide6-Addons, PySide6-Essentials, shiboken6 -> $PYSIDE_VERSION"
  fi

  step "Full app: PyInstaller and .deb (build_deb_linux.sh)"
  PYTHON_BIN="$PYTHON_BIN" \
  RUNTIME_REQUIREMENTS="$ARM_REQUIREMENTS" \
  BUILD_VENV="$ROOT/.venv-build-arm64" \
  VERSION="$VERSION" \
    bash "$ROOT/packaging/linux/build_deb_linux.sh"
  [ -f "$MAIN_DEB" ] || die "build_deb_linux.sh finished, but $MAIN_DEB is missing."

  APP_EXE="$ROOT/dist/$APP_ID/$APP_ID"
  file -b "$APP_EXE" | grep -q "ARM aarch64" || die "$APP_EXE is not an ARM64 executable: $(file -b "$APP_EXE")"

  if [ "$SMOKE_TEST" = 1 ]; then
    # On a desktop the windows show for 10 s each; without one, off screen
    # (smoke_test_packaged.py's default).
    if [ -z "${QT_QPA_PLATFORM:-}" ]; then
      if [ -n "${WAYLAND_DISPLAY:-}" ]; then export QT_QPA_PLATFORM=wayland
      elif [ -n "${DISPLAY:-}" ]; then export QT_QPA_PLATFORM=xcb
      fi
    fi
    step "Smoke test: the CME helper (QtWebEngine), then the main window (${QT_QPA_PLATFORM:-offscreen})"
    "$PYTHON_BIN" "$ROOT/scripts/smoke_test_packaged.py" --exe "$APP_EXE" --timeout 10 \
      || die "The packaged app did not stay up (run $APP_EXE in a terminal to see why), or set SMOKE_TEST=0."
  fi
fi

# --- Lite ----------------------------------------------------------------------
if [ "$SKIP_LITE" != 1 ]; then
  step "Lite: cargo and .deb (lite/packaging/linux/build_deb_linux.sh)"
  bash "$ROOT/lite/packaging/linux/build_deb_linux.sh"
  [ -f "$LITE_DEB" ] || die "Lite's build finished, but $LITE_DEB is missing."
fi

# --- Every ELF file runs on MAX_GLIBC -----------------------------------------
step "Checking the packages need no glibc newer than $MAX_GLIBC"
CHECK_DIR="$WORK/deb-contents"
DEBS=()
[ "$SKIP_MAIN" != 1 ] && DEBS+=("$MAIN_DEB")
[ "$SKIP_LITE" != 1 ] && DEBS+=("$LITE_DEB")
for deb in "${DEBS[@]}"; do
  rm -rf "$CHECK_DIR"; mkdir -p "$CHECK_DIR"
  dpkg-deb -x "$deb" "$CHECK_DIR"
  [ "$(dpkg-deb -f "$deb" Architecture)" = "arm64" ] || die "$deb is not marked arm64."
  newest="" ; worst=""
  while IFS= read -r -d '' f; do
    head -c 4 "$f" 2>/dev/null | grep -q $'\x7fELF' || continue
    v="$(objdump -T "$f" 2>/dev/null | grep -oE 'GLIBC_2\.[0-9]+' | sed 's/GLIBC_//' | sort -V | tail -n1 || true)"
    [ -n "$v" ] || continue
    if [ -z "$newest" ] || version_gt "$v" "$newest"; then newest="$v"; worst="${f#"$CHECK_DIR"}"; fi
  done < <(find "$CHECK_DIR" -type f -print0)
  echo "$(basename "$deb"): newest glibc symbol ${newest:-none} (${worst:-})"
  if [ -n "$newest" ] && version_gt "$newest" "$MAX_GLIBC"; then
    die "$(basename "$deb") needs glibc $newest ($worst), newer than $MAX_GLIBC: it will not install on Ubuntu 22.04."
  fi
done
rm -rf "$CHECK_DIR"

step "Done"
[ "$SKIP_MAIN" != 1 ] && echo "Full app: $MAIN_DEB"
[ "$SKIP_LITE" != 1 ] && echo "Lite:     $LITE_DEB"
echo "Install:  sudo apt install -y ./<file>.deb"
