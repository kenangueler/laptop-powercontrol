#!/bin/bash
# Install or uninstall Laptop Power Control system-wide.
#   sudo ./install.sh                     install or update under /usr/local
#   sudo ./install.sh --uninstall         remove it again (the generated TLP config is kept)
#   PREFIX=/usr DESTDIR=pkg ./install.sh  stage into a directory (used for packaging, no root needed)
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${PREFIX:-/usr/local}"
DESTDIR="${DESTDIR:-}"
APP_ID=com.kenangueler.apps.LaptopPowerControl
LIB="$PREFIX/lib/laptop-powercontrol"
BIN="$PREFIX/bin/powercontrol"
ICON="$PREFIX/share/icons/hicolor/scalable/apps/$APP_ID.svg"
DESKTOP="$PREFIX/share/applications/$APP_ID.desktop"
POLICY="/usr/share/polkit-1/actions/$APP_ID.policy"  # polkit only reads this directory

if [ -z "$DESTDIR" ] && [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo $0 $*" >&2
    exit 1
fi

refresh() {
    [ -z "$DESTDIR" ] || return 0
    if command -v update-desktop-database >/dev/null; then update-desktop-database -q "$PREFIX/share/applications" || true; fi
    if command -v gtk-update-icon-cache >/dev/null; then gtk-update-icon-cache -q -t "$PREFIX/share/icons/hicolor" || true; fi
}

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$DESTDIR$LIB" "$DESTDIR$BIN" "$DESTDIR$ICON" "$DESTDIR$DESKTOP" "$DESTDIR$POLICY"
    refresh
    echo "Uninstalled. To also drop the generated TLP config, use System > Remove generated configuration first."
    exit 0
fi

if [ -z "$DESTDIR" ]; then
    python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")' 2>/dev/null \
        || { echo "Missing Python bindings for GTK 4 / libadwaita, see README.md" >&2; exit 1; }
    command -v tlp >/dev/null || echo "Warning: TLP not found, settings will only last until the next reboot." >&2
fi

install -d -m 755 "$DESTDIR$LIB/powerctl" "$DESTDIR$PREFIX/bin"
install -m 755 "$SRC/powercontrol.py" "$DESTDIR$LIB/powercontrol.py"
install -m 644 "$SRC"/powerctl/*.py "$DESTDIR$LIB/powerctl/"
rm -rf "$DESTDIR$LIB/powerctl/__pycache__"
ln -sf "$LIB/powercontrol.py" "$DESTDIR$BIN"
install -D -m 644 "$SRC/data/$APP_ID.svg" "$DESTDIR$ICON"
install -D -m 644 "$SRC/data/$APP_ID.desktop" "$DESTDIR$DESKTOP"
install -d -m 755 "$(dirname "$DESTDIR$POLICY")"
sed "s#/usr/local/lib/laptop-powercontrol#$LIB#" "$SRC/data/$APP_ID.policy" > "$DESTDIR$POLICY"
chmod 644 "$DESTDIR$POLICY"
refresh
if [ -z "$DESTDIR" ] && command -v restorecon >/dev/null; then
    restorecon -R "$LIB" "$POLICY" "$DESKTOP" "$ICON" 2>/dev/null || true
fi
[ -n "$DESTDIR" ] || echo "Installed. Start 'Laptop Power Control' from the application menu or run: powercontrol"
