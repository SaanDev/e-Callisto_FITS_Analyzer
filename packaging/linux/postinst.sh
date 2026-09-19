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
