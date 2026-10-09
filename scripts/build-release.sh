#!/bin/bash
# Build release artifacts into dist/:
#   laptop-powercontrol-VERSION.tar.gz        source
#   laptop-powercontrol_VERSION_all.deb       Debian / Ubuntu package
#   Laptop_Power_Control-VERSION-x86_64.AppImage
#   SHA256SUMS
#
#   scripts/build-release.sh                  build everything (needs dpkg-deb, downloads appimagetool)
#   FORMATS="tar deb" scripts/build-release.sh
#
# The AppImage bundles only this app. Python 3, PyGObject, GTK 4 and libadwaita come from the system.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
FORMATS="${FORMATS:-tar deb appimage}"
NAME=laptop-powercontrol
VERSION="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from powerctl.core import VERSION; print(VERSION)' "$ROOT")"
MAINTAINER="Kenan Güler <15689800+kenangueler@users.noreply.github.com>"
SUMMARY="Per power source (AC / battery) laptop power settings, built on TLP"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$DIST"
cd "$ROOT"

build_tar() {
    local out="$DIST/$NAME-$VERSION.tar.gz"
    git ls-files -z | tar --null -T - --transform "s#^#$NAME-$VERSION/#" --owner=0 --group=0 \
        --sort=name --mtime="@$(git log -1 --format=%ct 2>/dev/null || date +%s)" -czf "$out"
    echo "  $(basename "$out")"
}

build_deb() {
    command -v dpkg-deb >/dev/null || { echo "dpkg-deb is required for the .deb" >&2; exit 1; }
    local pkg="$TMP/deb" out="$DIST/${NAME}_${VERSION}_all.deb"
    PREFIX=/usr DESTDIR="$pkg" "$ROOT/install.sh"
    install -D -m 644 LICENSE "$pkg/usr/share/doc/$NAME/copyright"
    mkdir -p "$pkg/DEBIAN"
    cat > "$pkg/DEBIAN/control" <<CONTROL
Package: $NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.9), python3-gi, python3-gi-cairo, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.5), pkexec | policykit-1
Recommends: tlp
Installed-Size: $(du -sk "$pkg/usr" | cut -f1)
Maintainer: $MAINTAINER
Homepage: https://github.com/kenangueler/laptop-powercontrol
Description: $SUMMARY
 GTK 4 / libadwaita app to tune CPU, platform profile, device power saving
 and battery charge thresholds separately for AC and battery power.
CONTROL
    printf '#!/bin/sh\nset -e\nrm -rf /usr/lib/%s/powerctl/__pycache__\n' "$NAME" > "$pkg/DEBIAN/prerm"
    chmod 755 "$pkg/DEBIAN/prerm"
    dpkg-deb --root-owner-group -Zxz --build "$pkg" "$out" >/dev/null
    echo "  $(basename "$out")"
}

build_appimage() {
    local tool="${APPIMAGETOOL:-$TMP/appimagetool}" app="$TMP/AppDir"
    local out="$DIST/Laptop_Power_Control-$VERSION-x86_64.AppImage"
    if [ ! -x "$tool" ]; then
        curl -fsSL -o "$tool" "$APPIMAGETOOL_URL"
        chmod +x "$tool"
    fi
    PREFIX=/usr DESTDIR="$app" "$ROOT/install.sh"
    rm -rf "$app/usr/share/polkit-1"  # an AppImage cannot install a polkit policy
    cp data/com.kenangueler.apps.LaptopPowerControl.desktop data/com.kenangueler.apps.LaptopPowerControl.svg "$app/"
    cp data/com.kenangueler.apps.LaptopPowerControl.svg "$app/.DirIcon"
    cat > "$app/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
PYTHON=/usr/bin/python3
[ -x "$PYTHON" ] || PYTHON=python3
export XDG_DATA_DIRS="$HERE/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
exec "$PYTHON" "$HERE/usr/lib/laptop-powercontrol/powercontrol.py" "$@"
APPRUN
    chmod +x "$app/AppRun"
    ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$tool" --no-appstream "$app" "$out" >"$TMP/appimagetool.log" 2>&1 \
        || { cat "$TMP/appimagetool.log" >&2; exit 1; }
    echo "  $(basename "$out")"
}

echo "Building $NAME $VERSION"
for f in $FORMATS; do "build_$f"; done
(cd "$DIST" && rm -f SHA256SUMS && sha256sum -- * > SHA256SUMS)
