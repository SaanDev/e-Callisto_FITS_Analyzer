#!/bin/sh
# postrm of e-callisto-fits-analyzer (build_deb_linux.sh): refresh the menu
# and icon caches, and on removal take away the desktop launcher postinst.sh
# made for the removing user.
set -e
PKG="e-callisto-fits-analyzer"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q /usr/share/icons/hicolor || true
fi

case "${1:-}" in
  remove|purge) ;;
  *) exit 0 ;;
esac

USER_NAME="${SUDO_USER:-}"
if [ -z "$USER_NAME" ] && [ -n "${PKEXEC_UID:-}" ]; then
  USER_NAME="$(getent passwd "$PKEXEC_UID" | cut -d: -f1)"
fi
[ -n "$USER_NAME" ] && [ "$USER_NAME" != "root" ] || exit 0
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
[ -n "$USER_HOME" ] || exit 0

DESKTOP_DIR="$(runuser -u "$USER_NAME" -- env HOME="$USER_HOME" xdg-user-dir DESKTOP 2>/dev/null || true)"
for dir in "$DESKTOP_DIR" "$USER_HOME/Desktop"; do
  [ -n "$dir" ] && rm -f "$dir/$PKG.desktop"
done
exit 0
