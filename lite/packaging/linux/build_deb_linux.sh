#!/usr/bin/env bash
# Build the .deb for e-CALLISTO FITS Analyzer Lite, beside the full app's
# (packaging/linux/build_deb_linux.sh): its own package name, command,
# desktop entry and icon, so both can be installed.
#
# Usage, from anywhere on Debian/Ubuntu with rustup and dpkg-deb:
#   bash lite/packaging/linux/build_deb_linux.sh
# Optional:
#   VERSION=3.2.0-lite the package version (default: Lite's Cargo version)
#
# The file is e-callisto-fits-analyzer_<version>_<arch>.deb, the name the
# update check looks for in the release tag; the package inside is
# e-callisto-fits-analyzer-lite, so it installs beside the full app.
set -euo pipefail

LITE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ROOT="$(cd "$LITE/.." && pwd)"
PKG="e-callisto-fits-analyzer-lite"
APP_NAME="e-CALLISTO FITS Analyzer Lite"
VERSION="${VERSION:-$(awk -F'"' '/^version[[:space:]]*=/{print $2; exit}' "$LITE/Cargo.toml")}"
ARCH="$(dpkg --print-architecture)"
TARGET_DIR="${CARGO_TARGET_DIR:-$LITE/target}"
DIST="$LITE/dist"
STAGE="$DIST/deb"
OUT="$DIST/e-callisto-fits-analyzer_${VERSION}_${ARCH}.deb"

echo "==> Building $APP_NAME $VERSION ($ARCH)"
cd "$LITE"
cargo build --release -p callisto-lite

echo "==> Staging"
rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" "$STAGE/usr/bin" "$STAGE/usr/share/applications" "$STAGE/usr/share/doc/$PKG"
install -m 755 "$TARGET_DIR/release/callisto-lite" "$STAGE/usr/bin/$PKG"
strip "$STAGE/usr/bin/$PKG" || true
# The icon at hicolor's sizes (16 to 512): desktops look nowhere else, and
# show a gear instead.
cargo run --release -q -p callisto-lite --example export_icons -- \
  "$ROOT/assets/branding/FITS_analyzer.png" "$STAGE/usr/share/icons/hicolor" "$PKG"
install -m 644 "$ROOT/LICENSE" "$STAGE/usr/share/doc/$PKG/copyright"
install -m 644 "$LITE/crates/callisto-core/assets/fonts/LICENSE_DEJAVU" "$STAGE/usr/share/doc/$PKG/LICENSE_DEJAVU_FONTS"

# The window's app id is e-callisto-fits-analyzer-lite (lib.rs), so Wayland
# docks match the window to this entry.
cat > "$STAGE/usr/share/applications/$PKG.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_NAME
Comment=View and analyse e-CALLISTO solar radio spectra
Exec=$PKG
Icon=$PKG
Terminal=false
Categories=Science;Education;Astronomy;
StartupNotify=true
StartupWMClass=$PKG
EOF

SIZE_KB="$(du -sk "$STAGE/usr" | cut -f1)"
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: science
Priority: optional
Architecture: $ARCH
Installed-Size: $SIZE_KB
Depends: libc6, libgcc-s1, libxkbcommon0, libxkbcommon-x11-0, libx11-6, libxcursor1, libxrandr2, libxi6, libwayland-client0, libgl1, libvulkan1 | mesa-vulkan-drivers, fontconfig
Recommends: xdg-desktop-portal, fonts-dejavu-core
Maintainer: Sahan S Liyanage <sahanslst@gmail.com>
Homepage: https://ecallistoanalyzer.org/
Description: e-CALLISTO FITS Analyzer Lite
 The radio part of the e-CALLISTO FITS Analyzer: load, combine, clean and
 analyse e-CALLISTO solar radio spectra, download from the archive, and
 export figures, projects and reports. It installs beside the full app.
EOF

# Line endings stripped: a checkout made on Windows has CRLF, which breaks
# the #!/bin/sh line.
for script in postinst postrm; do
  tr -d '\r' < "$LITE/packaging/linux/$script.sh" > "$STAGE/DEBIAN/$script"
  chmod 755 "$STAGE/DEBIAN/$script"
done

echo "==> Packaging"
rm -f "$OUT"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT"
echo "==> Done: $OUT"
