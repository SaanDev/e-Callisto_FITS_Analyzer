#!/usr/bin/env bash
set -euo pipefail

# Build a .deb package for Ubuntu/Linux
# Usage:
#   bash src/Installation/build_deb_linux.sh
# Optional overrides:
#   ROOT=/path/to/repo VERSION=2.2-dev bash src/Installation/build_deb_linux.sh

ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
APP_ID="e-callisto-fits-analyzer"
APP_NAME="e-CALLISTO FITS Analyzer"
VERSION_FILE="$ROOT/src/version.py"
DEFAULT_VERSION="$(awk -F'\"' '/^APP_VERSION[[:space:]]*=[[:space:]]*\"/{print $2; exit}' "$VERSION_FILE" 2>/dev/null || true)"
VERSION="${VERSION:-$DEFAULT_VERSION}"
ARCH="$(dpkg --print-architecture)"
OUT_DEB="$ROOT/dist/${APP_ID}_${VERSION}_${ARCH}.deb"
RUNTIME_REQUIREMENTS="$ROOT/src/Installation/requirements-runtime.txt"
BUILD_REQUIREMENTS="$ROOT/src/Installation/requirements-build.txt"

cd "$ROOT"

echo "==> Project root: $ROOT"
echo "==> Building version: $VERSION ($ARCH)"

if [ -z "${VERSION}" ]; then
  echo "Could not determine APP_VERSION from $VERSION_FILE. Set VERSION manually." >&2
  exit 1
fi

# 1) Build app folder with PyInstaller
if [ ! -f "$RUNTIME_REQUIREMENTS" ]; then
  echo "Missing runtime requirements file: $RUNTIME_REQUIREMENTS" >&2
  exit 1
fi
if [ ! -f "$BUILD_REQUIREMENTS" ]; then
  echo "Missing build requirements file: $BUILD_REQUIREMENTS" >&2
  exit 1
fi

python3 -m venv "$ROOT/.venv-build"
source "$ROOT/.venv-build/bin/activate"
python -m pip install --upgrade pip
python -m pip install --requirement "$BUILD_REQUIREMENTS"
python -m pip install --requirement "$RUNTIME_REQUIREMENTS"
pyinstaller --clean --noconfirm "$ROOT/src/Installation/FITS_Analyzer_linux.spec"

# 2) Install fpm (packager) if missing
if ! command -v fpm >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y ruby ruby-dev build-essential desktop-file-utils
  sudo gem install --no-document fpm
fi

# 3) Prepare package helper files
mkdir -p "$ROOT/packaging"

cat > "$ROOT/packaging/${APP_ID}.sh" <<'EOF'
#!/bin/sh
exec /opt/e-callisto-fits-analyzer/e-callisto-fits-analyzer "$@"
EOF
chmod 755 "$ROOT/packaging/${APP_ID}.sh"

cat > "$ROOT/packaging/${APP_ID}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=${APP_NAME}
Exec=${APP_ID}
Icon=${APP_ID}
Terminal=false
Categories=Science;Education;
StartupNotify=true
EOF

cat > "$ROOT/packaging/postinst.sh" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q /usr/share/icons/hicolor || true
fi

# Create desktop shortcut for sudo-invoking user (if available)
if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
  USER_HOME="$(getent passwd "${SUDO_USER}" | cut -d: -f6)"
  if [ -n "${USER_HOME}" ] && [ -d "${USER_HOME}/Desktop" ]; then
    install -Dm755 /usr/share/applications/e-callisto-fits-analyzer.desktop \
      "${USER_HOME}/Desktop/e-callisto-fits-analyzer.desktop" || true
    chown "${SUDO_USER}:${SUDO_USER}" \
      "${USER_HOME}/Desktop/e-callisto-fits-analyzer.desktop" || true
  fi
fi
exit 0
EOF
chmod 755 "$ROOT/packaging/postinst.sh"

# 4) Build .deb with all files included
fpm -s dir -t deb \
  -n "$APP_ID" \
  -v "$VERSION" \
  --architecture "$ARCH" \
  --maintainer "Sahan S Liyanage <sahanslst@gmail.com>" \
  --description "e-CALLISTO FITS Analyzer desktop application" \
  --depends libgl1 \
  --depends libegl1 \
  --depends libxkbcommon-x11-0 \
  --depends libxcb-cursor0 \
  --after-install "$ROOT/packaging/postinst.sh" \
  -p "$OUT_DEB" \
  "$ROOT/dist/e-callisto-fits-analyzer/=/opt/e-callisto-fits-analyzer/" \
  "$ROOT/packaging/${APP_ID}.sh=/usr/bin/${APP_ID}" \
  "$ROOT/packaging/${APP_ID}.desktop=/usr/share/applications/${APP_ID}.desktop" \
  "$ROOT/assets/FITS_analyzer.png=/usr/share/icons/hicolor/256x256/apps/${APP_ID}.png"

echo "Built: $OUT_DEB"
echo "Install: sudo apt install -y \"$OUT_DEB\""
echo "Run: $APP_ID"
