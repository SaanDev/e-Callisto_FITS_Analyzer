#!/bin/sh
# postinst of e-callisto-fits-analyzer-lite (build_deb_linux.sh): refresh the
# menu and icon caches, and put a launcher on the installing user's desktop,
# as the full app's package does.
set -e
PKG="e-callisto-fits-analyzer-lite"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q /usr/share/icons/hicolor || true
fi

[ "${1:-}" = "configure" ] || exit 0

# Who installed it: sudo apt/dpkg, or pkexec. Package managers running as
# root alone (App Center) leave no user, and no desktop launcher.
USER_NAME="${SUDO_USER:-}"
if [ -z "$USER_NAME" ] && [ -n "${PKEXEC_UID:-}" ]; then
  USER_NAME="$(getent passwd "$PKEXEC_UID" | cut -d: -f1)"
fi
[ -n "$USER_NAME" ] && [ "$USER_NAME" != "root" ] || exit 0
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
[ -n "$USER_HOME" ] || exit 0

# The desktop folder as the user names it (Desktop, Escritorio, ...).
DESKTOP_DIR="$(runuser -u "$USER_NAME" -- env HOME="$USER_HOME" xdg-user-dir DESKTOP 2>/dev/null || true)"
if [ -z "$DESKTOP_DIR" ] || [ ! -d "$DESKTOP_DIR" ]; then
  DESKTOP_DIR="$USER_HOME/Desktop"
fi
[ -d "$DESKTOP_DIR" ] || exit 0

SHORTCUT="$DESKTOP_DIR/$PKG.desktop"
install -m 755 -o "$USER_NAME" -g "$(id -g "$USER_NAME")" "/usr/share/applications/$PKG.desktop" "$SHORTCUT" || exit 0
# GNOME's desktop runs a launcher only once it is trusted ("Allow Launching").
USER_UID="$(id -u "$USER_NAME")"
runuser -u "$USER_NAME" -- env HOME="$USER_HOME" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$USER_UID/bus" \
  gio set "$SHORTCUT" metadata::trusted true >/dev/null 2>&1 || true
exit 0
