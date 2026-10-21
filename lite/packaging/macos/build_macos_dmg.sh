#!/usr/bin/env bash
# Build "e-CALLISTO FITS Analyzer Lite.app" and its .dmg, beside the full
# app's (packaging/macos/build_macos_dmg.sh): its own bundle identifier,
# name and disk image.
#
# Usage, from anywhere on a Mac with Xcode's command line tools and rustup:
#   bash lite/packaging/macos/build_macos_dmg.sh
# Optional:
#   VERSION=1.0.0                         the bundle version (default: Lite's Cargo version)
#   UNIVERSAL=0                           build for this Mac's architecture only
#   CODESIGN_IDENTITY="Developer ID Application: ..."   sign for distribution (default: ad hoc)
#   NOTARY_PROFILE=<keychain profile>     notarise and staple the .dmg
#                                         (xcrun notarytool store-credentials <profile> ...)
set -euo pipefail

LITE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ROOT="$(cd "$LITE/.." && pwd)"
APP_NAME="e-CALLISTO FITS Analyzer Lite"
BUNDLE_ID="com.sahansliyanage.callisto.fitsanalyzer.lite"
VERSION="${VERSION:-$(awk -F'"' '/^version[[:space:]]*=/{print $2; exit}' "$LITE/Cargo.toml")}"
UNIVERSAL="${UNIVERSAL:-1}"
TARGET_DIR="${CARGO_TARGET_DIR:-$LITE/target}"
DIST="$LITE/dist"
APP="$DIST/$APP_NAME.app"
DMG="$DIST/e-CALLISTO_FITS_Analyzer_Lite_v${VERSION}_macOS.dmg"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-11.0}"

echo "==> Building $APP_NAME $VERSION"
cd "$LITE"
if [ "$UNIVERSAL" = "1" ]; then
  rustup target add aarch64-apple-darwin x86_64-apple-darwin
  cargo build --release -p callisto-lite --target aarch64-apple-darwin
  cargo build --release -p callisto-lite --target x86_64-apple-darwin
  BIN="$TARGET_DIR/callisto-lite-universal"
  lipo -create -output "$BIN" \
    "$TARGET_DIR/aarch64-apple-darwin/release/callisto-lite" \
    "$TARGET_DIR/x86_64-apple-darwin/release/callisto-lite"
else
  cargo build --release -p callisto-lite
  BIN="$TARGET_DIR/release/callisto-lite"
fi

echo "==> Assembling the bundle"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/callisto-lite"
cp "$ROOT/assets/branding/icon.icns" "$APP/Contents/Resources/icon.icns"
cp "$ROOT/LICENSE" "$APP/Contents/Resources/LICENSE.txt"
cp "$LITE/crates/callisto-core/assets/fonts/LICENSE_DEJAVU" "$APP/Contents/Resources/LICENSE_DEJAVU_FONTS.txt"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleExecutable</key><string>callisto-lite</string>
  <key>CFBundleIconFile</key><string>icon.icns</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>LSMinimumSystemVersion</key><string>$MACOSX_DEPLOYMENT_TARGET</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.education</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>2026 Sahan S Liyanage, Astronomical and Space Science Unit, University of Colombo</string>
</dict>
</plist>
EOF

echo "==> Signing (${CODESIGN_IDENTITY:-ad hoc})"
codesign --force --options runtime --timestamp=none --sign "${CODESIGN_IDENTITY:--}" "$APP/Contents/MacOS/callisto-lite"
if [ -n "${CODESIGN_IDENTITY:-}" ]; then
  codesign --force --options runtime --timestamp --sign "$CODESIGN_IDENTITY" "$APP"
else
  codesign --force --sign - "$APP"
fi
codesign --verify --deep --strict --verbose=2 "$APP"

echo "==> Disk image"
rm -f "$DMG"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGING" -fs HFS+ -format UDZO -imagekey zlib-level=9 "$DMG"
if [ -n "${CODESIGN_IDENTITY:-}" ]; then
  codesign --force --timestamp --sign "$CODESIGN_IDENTITY" "$DMG"
fi

if [ -n "${NOTARY_PROFILE:-}" ]; then
  if [ -z "${CODESIGN_IDENTITY:-}" ]; then
    echo "NOTARY_PROFILE needs CODESIGN_IDENTITY (a Developer ID Application certificate)." >&2
    exit 1
  fi
  echo "==> Notarising"
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
  xcrun stapler validate "$DMG"
fi

echo "==> Done"
echo "    App: $APP"
echo "    DMG: $DMG"
