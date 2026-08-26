#!/usr/bin/env bash
set -euo pipefail

# Build the macOS .app bundle (py2app) and wrap it in a drag-to-install .dmg.
# Usage:
#   bash src/Installation/build_macos_dmg.sh
# Optional overrides:
#   ROOT=/path/to/repo VERSION=2.8.0 PYTHON_BIN=/path/to/python3 \
#   SKIP_APP=1 bash src/Installation/build_macos_dmg.sh
#
# SKIP_APP=1 reuses an existing dist/*.app and only rebuilds the disk image.

ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
APP_ID="e-callisto-fits-analyzer"
APP_NAME="e-Callisto FITS Analyzer"
VERSION_FILE="$ROOT/src/version.py"
DEFAULT_VERSION="$(awk -F'"' '/^APP_VERSION[[:space:]]*=[[:space:]]*"/{print $2; exit}' "$VERSION_FILE" 2>/dev/null || true)"
VERSION="${VERSION:-$DEFAULT_VERSION}"
PYTHON_BIN="${PYTHON_BIN:-}"
SKIP_APP="${SKIP_APP:-0}"
ARCH="$(uname -m)"
APP_BUNDLE="$ROOT/dist/${APP_NAME}.app"
OUT_DMG="$ROOT/dist/${APP_ID}_${VERSION}_macOS_${ARCH}.dmg"
DMG_SETTINGS="$ROOT/src/Installation/dmg_settings.py"
ICON_FILE="$ROOT/assets/icon.icns"

cd "$ROOT"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This script builds a macOS bundle and must run on macOS." >&2
  exit 1
fi

if [ -z "$VERSION" ]; then
  echo "Could not determine APP_VERSION from $VERSION_FILE. Set VERSION manually." >&2
  exit 1
fi

echo "==> Project root: $ROOT"
echo "==> Building version: $VERSION (macOS $ARCH)"

if [ -z "$PYTHON_BIN" ]; then
  if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "${VIRTUAL_ENV}/bin/python" ]; then
    PYTHON_BIN="${VIRTUAL_ENV}/bin/python"
  elif [ -x "$ROOT/.venv/bin/python" ]; then
    PYTHON_BIN="$ROOT/.venv/bin/python"
  else
    PYTHON_BIN="$(command -v python3 || true)"
  fi
fi

if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
  echo "Could not find a usable Python interpreter. Set PYTHON_BIN=/path/to/python3." >&2
  exit 1
fi

PYTHON_VERSION="$("$PYTHON_BIN" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
PYTHON_OK="$("$PYTHON_BIN" -c 'import sys; print(int(sys.version_info >= (3, 12)))')"

if [ "$PYTHON_OK" != "1" ]; then
  cat >&2 <<EOF
Python $PYTHON_VERSION is too old for the macOS build.
Use Python 3.12+, for example:
  PYTHON_BIN=/opt/homebrew/bin/python3.13 bash src/Installation/build_macos_dmg.sh
EOF
  exit 1
fi

echo "==> Python: $PYTHON_BIN ($PYTHON_VERSION)"

for module in py2app dmgbuild; do
  if ! "$PYTHON_BIN" -c "import $module" >/dev/null 2>&1; then
    echo "Missing build dependency '$module'. Install it with:" >&2
    echo "  $PYTHON_BIN -m pip install $module" >&2
    exit 1
  fi
done

if [ ! -f "$ICON_FILE" ]; then
  echo "Missing application icon: $ICON_FILE" >&2
  exit 1
fi

# --- 1. Application bundle -------------------------------------------------
if [ "$SKIP_APP" = "1" ]; then
  echo "==> SKIP_APP=1, reusing existing bundle"
  if [ ! -d "$APP_BUNDLE" ]; then
    echo "No existing bundle at $APP_BUNDLE. Re-run without SKIP_APP=1." >&2
    exit 1
  fi
else
  echo "==> Cleaning previous build output"
  rm -rf "$ROOT/build" "$APP_BUNDLE"

  echo "==> Running py2app"
  "$PYTHON_BIN" "$ROOT/src/Installation/setup.py" py2app

  if [ ! -d "$APP_BUNDLE" ]; then
    echo "py2app finished but $APP_BUNDLE was not produced." >&2
    exit 1
  fi
fi

# --- 2. Ad-hoc signature ---------------------------------------------------
# `codesign --deep` is NOT enough here. py2app's macholib pass rewrites load
# commands in every bundled library, which invalidates their signatures, but
# --deep only walks bundle code locations and never descends into
# Contents/Resources/lib/pythonX.Y where PySide6's Qt frameworks live. The app
# then launches fine until it loads QtWebEngineCore, and AMFI kills it with
# SIGKILL (Code Signature Invalid). Sign every Mach-O inside-out instead.
#
# Ad-hoc ("-") is enough for local and self-distributed builds; pass
# CODESIGN_IDENTITY="Developer ID Application: ..." to notarize a public release.
echo "==> Signing the bundle"
"$PYTHON_BIN" "$ROOT/src/Installation/codesign_macos_bundle.py" \
  "$APP_BUNDLE" --identity "${CODESIGN_IDENTITY:--}"

codesign --verify --strict "$APP_BUNDLE" && echo "    signature OK"

# --- 3. Disk image ---------------------------------------------------------
# dmgbuild gives the nicer window (background, icon positions) but works by
# creating a read-write image, populating it, then converting to UDZO, so it
# needs roughly 2.5x the bundle size in free space. When the volume is tighter
# than that, fall back to streaming straight into a compressed image, which
# only needs room for the finished .dmg.
echo "==> Building disk image"
rm -f "$OUT_DMG"

APP_KB="$(du -sk "$APP_BUNDLE" | cut -f1)"
FREE_KB="$(df -k "$ROOT" | tail -1 | awk '{print $4}')"
STYLED_KB=$(( APP_KB * 5 / 2 ))
DMG_STYLE="${DMG_STYLE:-auto}"

echo "    bundle: $(( APP_KB / 1024 )) MB, free: $(( FREE_KB / 1024 )) MB"

if [ "$DMG_STYLE" != "plain" ] && [ "$FREE_KB" -ge "$STYLED_KB" ]; then
  echo "    using dmgbuild (styled layout)"
  "$PYTHON_BIN" -m dmgbuild \
    -s "$DMG_SETTINGS" \
    -D app="$APP_BUNDLE" \
    -D icon="$ICON_FILE" \
    "$APP_NAME $VERSION" \
    "$OUT_DMG"
else
  if [ "$DMG_STYLE" != "plain" ]; then
    echo "    not enough free space for dmgbuild (needs ~$(( STYLED_KB / 1024 )) MB)"
  fi
  echo "    streaming a compressed image with hdiutil (plain layout)"

  STAGING="$ROOT/dmg_tmp"
  rm -rf "$STAGING"
  mkdir -p "$STAGING"

  # Move rather than copy: same volume, so this costs no extra space. The trap
  # puts the bundle back if hdiutil fails part way through.
  restore_bundle() {
    if [ -d "$STAGING/$(basename "$APP_BUNDLE")" ]; then
      mv "$STAGING/$(basename "$APP_BUNDLE")" "$ROOT/dist/"
    fi
    rm -rf "$STAGING"
  }
  trap restore_bundle EXIT

  mv "$APP_BUNDLE" "$STAGING/"
  ln -s /Applications "$STAGING/Applications"

  hdiutil create \
    -volname "$APP_NAME $VERSION" \
    -srcfolder "$STAGING" \
    -format UDZO \
    -imagekey zlib-level=9 \
    -ov "$OUT_DMG"

  restore_bundle
  trap - EXIT
fi

if [ ! -f "$OUT_DMG" ]; then
  echo "dmgbuild finished but $OUT_DMG was not produced." >&2
  exit 1
fi

echo
echo "==> Done"
echo "    App: $APP_BUNDLE"
echo "    DMG: $OUT_DMG ($(du -h "$OUT_DMG" | cut -f1 | tr -d ' '))"
