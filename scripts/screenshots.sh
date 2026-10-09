#!/bin/bash
# Render docs/screenshots for the current version (core.VERSION). The folder is replaced, never
# versioned, so the repository only ever holds one set of images.
#
#   scripts/screenshots.sh               render in a clean Fedora container (docker or podman)
#   scripts/screenshots.sh --local       render with the host's mutter/GTK instead
#   scripts/screenshots.sh --if-changed  skip if docs/screenshots/VERSION already matches
#
# Images come from the simulated test machine in tests/fakeroot.py, never from the host.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/docs/screenshots"
IMAGE="${SCREENSHOT_IMAGE:-fedora:latest}"
PACKAGES="python3-gobject gtk4 libadwaita mutter dbus-daemon mesa-dri-drivers adwaita-icon-theme adwaita-sans-fonts pngquant"
LOCAL=0
IF_CHANGED=0
for arg in "$@"; do
    case "$arg" in
        --local) LOCAL=1 ;;
        --if-changed) IF_CHANGED=1 ;;
        -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

VERSION="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from powerctl.core import VERSION; print(VERSION)' "$ROOT")"
if [ "$IF_CHANGED" = 1 ] && [ "$(cat "$OUT/VERSION" 2>/dev/null)" = "$VERSION" ]; then
    echo "Screenshots are already up to date for $VERSION"
    exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
chmod 777 "$TMP"

if [ "$LOCAL" = 1 ]; then
    "$ROOT/tests/run_gui_tests.sh" --screenshots "$TMP"
    if command -v pngquant >/dev/null; then pngquant --force --ext .png --skip-if-larger --quality 80-95 "$TMP"/*/*.png || true; fi
else
    ENGINE="$(command -v podman || command -v docker || true)"
    [ -n "$ENGINE" ] || { echo "Neither podman nor docker found, use --local" >&2; exit 2; }
    "$ENGINE" run --rm -v "$ROOT":/src:ro,z -v "$TMP":/out:z "$IMAGE" bash -c "
        set -e
        dnf -y -q install $PACKAGES >/dev/null 2>&1 || { echo \"dnf install failed\" >&2; exit 1; }
        cp -r /src /work
        /work/scripts/screenshots.sh --local >/dev/null
        cp -r /work/docs/screenshots/. /out/
        chmod -R a+rwX /out"
fi

[ -s "$TMP/VERSION" ] || { echo "Rendering failed" >&2; exit 1; }
rm -rf "$OUT"
mkdir -p "$OUT"
cp -r "$TMP"/. "$OUT/"
echo "Rendered $(find "$OUT" -name "*.png" | wc -l) screenshots for $VERSION into docs/screenshots ($(du -sh "$OUT" | cut -f1))"
