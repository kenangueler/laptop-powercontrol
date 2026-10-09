#!/bin/bash
# Run tests/gui_test.py inside a private headless GNOME compositor (mutter), so no window appears on the
# desktop and no real display is needed. Arguments are passed through (e.g. --screenshots docs/screenshots).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
command -v mutter >/dev/null || { echo "mutter is required for headless GUI tests" >&2; exit 2; }

run() {
    export XDG_RUNTIME_DIR; XDG_RUNTIME_DIR="$(mktemp -d)"
    export GTK_A11Y=none GDK_BACKEND=wayland GSK_RENDERER=cairo
    unset DISPLAY
    local sock="powercontrol-test-$$"
    mutter --headless --wayland --no-x11 --virtual-monitor 1100x860 --wayland-display="$sock" \
        >"$XDG_RUNTIME_DIR/mutter.log" 2>&1 &
    local mp=$!
    for _ in $(seq 50); do [ -S "$XDG_RUNTIME_DIR/$sock" ] && break; sleep 0.1; done
    WAYLAND_DISPLAY="$sock" timeout 120 python3 "$HERE/gui_test.py" "$@"
    local rc=$?
    kill "$mp" 2>/dev/null; wait "$mp" 2>/dev/null
    rm -rf "$XDG_RUNTIME_DIR"
    return $rc
}

if [ "${_PC_IN_DBUS:-}" = 1 ]; then
    run "$@"
else
    _PC_IN_DBUS=1 exec dbus-run-session -- "$0" "$@" 2> >(grep -v -E "portal|dbus-daemon|AT-SPI|a11y|fuse|MESA|discover_other|secret|SSH_AUTH_SOCK|Lost connection|bus can.t be made|^$" >&2)
fi
