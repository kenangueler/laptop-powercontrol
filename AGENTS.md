# AGENTS.md

Notes for AI agents and contributors. The user-facing overview is in [README.md](README.md); everything
a developer needs beyond that is here.

## What this is

A GTK 4 / libadwaita Python app that manages laptop power settings per power source (AC and battery).
It writes a TLP drop-in for persistence and also applies the active column directly to sysfs. All
system changes go through a small privileged helper started with `pkexec`.

## Layout

| Path | Role |
|---|---|
| `powercontrol.py` | Thin launcher for a checkout or the installed copy; puts its dir on `sys.path` and calls `powerctl.cli.main` |
| `powerctl/cli.py` | Dispatch: GUI by default, CLI flags (`--status`, `--profile`, ...), helper actions (`--apply`, `--remove-config`). Also `python3 -m powerctl` and the pip entry point |
| `powerctl/core.py` | No GTK. Path helpers, hardware detection, settings model (`SETTINGS`), TLP config parse/render/conflicts, live status, `ProfileStore`, prefs, helper invocation |
| `powerctl/helper.py` | No GTK, **runs as root**. Validates arguments, writes the drop-in, disables overriding lines, runs `tlp start`, writes sysfs, verifies, prints a JSON result |
| `powerctl/gui.py` | `App`, `MainWindow` and the five pages (Overview, Power Settings, Profiles, Battery, System) |
| `powerctl/widgets.py` | `MatrixEditor` (the two-column editor), stat cards, charts, CSS |
| `tests/fakeroot.py` | Builds a fake sysfs/procfs/TLP tree of a generic laptop |
| `tests/test_core.py` | Unit and helper tests (no root, no display) |
| `tests/gui_test.py`, `tests/run_gui_tests.sh` | Headless end-to-end GUI test in a private mutter; also renders the screenshots |
| `data/` | Desktop entry, polkit policy (exec path is rewritten per prefix), app icon `com.kenangueler.apps.LaptopPowerControl.svg` |
| `install.sh` | Installs to `PREFIX` (default `/usr/local`); with `DESTDIR` it only stages files (used for packaging) |
| `scripts/screenshots.sh` | Renders `docs/screenshots/` in a Fedora container (or `--local`), replaces the folder, compresses with pngquant |
| `scripts/build-release.sh` | Builds `dist/`: source tarball, `.deb` (via `install.sh` + `dpkg-deb`), AppImage (via appimagetool), `SHA256SUMS` |
| `pyproject.toml` | Package metadata, version read from `powerctl.core.VERSION`, `powercontrol` console script |
| `.github/workflows/ci.yml` | Tests in a Fedora container; on `main` re-renders screenshots when the version changed |
| `.github/workflows/release.yml` | Only on a `release/*` tag (`release/X.Y.Z`): runs CI, checks tag == version, builds, smoke-tests and publishes a GitHub release |
| `docs/screenshots/` | Generated PNGs (light and dark) plus a `VERSION` stamp. Do not edit by hand |

## Commands

```bash
python3 -m unittest discover -s tests -v                 # always run
tests/run_gui_tests.sh                                   # GUI e2e, needs mutter + dbus-run-session
tests/run_gui_tests.sh --screenshots /tmp/shots          # render PNGs to inspect UI changes
python3 -m py_compile powercontrol.py powerctl/*.py tests/*.py
desktop-file-validate data/*.desktop
xmllint --noout data/*.policy data/*.svg
bash -n install.sh tests/run_gui_tests.sh scripts/*.sh
scripts/screenshots.sh                                   # refresh docs/screenshots (container)
FORMATS="tar deb" scripts/build-release.sh               # packages into dist/ (AppImage needs network)
```

Everything must pass before committing. No build step for development, no dependencies besides
PyGObject (on Debian/Ubuntu also `python3-gi-cairo`, needed by the charts).

## Hard rules

1. **Never touch the real system.** Do not run `sudo`, `pkexec`, `install.sh`, `tlp` or the helper on
   the host. Use the fake root and a temporary config dir:
   ```bash
   python3 tests/fakeroot.py /tmp/fr
   POWERCONTROL_SYSROOT=/tmp/fr XDG_CONFIG_HOME=/tmp/fr-cfg ./powercontrol.py
   ```
   `POWERCONTROL_SYSROOT` prefixes every system path (including `/proc`), skips `pkexec`, uses the fake
   tree's `/usr/sbin/tlp` stub and never asks the host's systemd. `POWERCONTROL_TLP` can override the
   `tlp` binary. Both are read when `powerctl.core` is imported.
2. **All system file access goes through `core.P()`** and the helpers built on it (`rd`, `rd_int`, `wr`,
   `exists`, `pglob`, `write_atomic`). That includes `/proc`. A raw `open("/sys/...")` breaks test
   isolation and leaks host data into tests and screenshots.
3. **The helper is a security boundary.** Every argument is whitelisted in `helper.parse()`, every value
   must match `VALUE_RE` and `Setting.valid()`, and nothing is written unless the whole request is
   valid. No shell, no commands built from input, no writes outside the paths listed below.
4. **`core.py` and `helper.py` must not import gi/GTK.** The helper runs as root without a display.
5. **The GUI never writes system files.** It calls `core.run_helper()` in a thread and handles the
   result with `GLib.idle_add`.
6. **Keep the repo machine-agnostic.** No home paths, hostnames or real hardware identifiers in code,
   tests, docs or screenshots. The author name appears only in `LICENSE` and the About dialog. Install paths are `/usr/local/...`, the desktop
   entry uses `Exec=powercontrol`.
7. **Writing style:** no em dashes anywhere (code, UI strings, docs, commits). Keep docs short and plain.

## How it works

**Settings model.** Each `Setting` in `core.SETTINGS` has a key, a TLP prefix (`_ON_AC`/`_ON_BAT` is
appended), a kind (`choice`, `bool`, `pct`), a section, and optionally `requires` (a sysfs path that must
exist), `needs_tlp`, `choices` (list or callable reading the hardware), `on`/`off` values, `lo`/`hi`.
The editor rows, drop-in rendering, conflict detection, helper validation and profile cleaning are all
derived from this list.

**State.** `{"ac": {key: value}, "bat": {...}, "battery": {"start": int, "stop": int}, "profile": name}`.
Values are strings except thresholds. `core.load_system_state()` reads the effective TLP config in TLP's
order (`/usr/share/tlp/defaults.conf`, `/etc/tlp.d/*.conf` sorted, `/etc/tlp.conf` last) and falls back
to live sysfs values. The GUI compares the editor against a `baseline` taken right after loading to
decide whether there are unapplied changes.

**TLP precedence.** `/etc/tlp.conf` is read after the drop-ins and wins. `core.find_conflicts()` lists
managed keys set there (or in drop-ins sorted after ours). With "Disable overriding entries" the helper
prefixes those lines with `#[powercontrol] ` and backs up `/etc/tlp.conf` once.
`--remove-config` undoes both.

**Helper protocol.** Arguments: `ac.KEY=V`, `bat.KEY=V`, `start=N`, `stop=N`, `charge_behaviour=V`,
`profile=NAME`, `--persist`, `--fix-conflicts`. Order of work: validate everything, write the drop-in
atomically, handle conflicts, `tlp start`, write the active column to sysfs (min/max perf in a safe
order), optional charge behaviour, read back and warn on mismatches. Output is one line:
`POWERCONTROL-RESULT {"ok", "errors", "warnings", "notes", "column"}`. Exit 1 on errors.
The helper may only write `/etc/tlp.d/99-powercontrol.conf`, prefix/unprefix lines in TLP config files,
`/etc/tlp.conf.powercontrol-backup`, and the sysfs attributes behind the settings and thresholds.

**Profiles.** `ProfileStore` keeps built-ins from `core.builtin_profiles()` (values picked with `_mk()`
from ordered fallbacks the hardware supports) plus `~/.config/laptop-powercontrol/profiles.json`
(`version: 2`): `profiles` (custom, each with `history` newest first, max 15, and `original`) and
`builtin_overrides` (user edits to built-ins). Revert pops history, reset restores the original or drops
the override, reset-all writes a timestamped `.bak` first. Every destructive GUI action takes a
`snapshot()` for Undo. Older files without these fields must keep loading. UI prefs live in
`settings.json` (`persist`, `fix_conflicts`, `active_profile`).

**Install and helper invocation.** `install.sh` puts the code root-owned in `$PREFIX/lib/laptop-powercontrol`
(`/usr/local` by default, `/usr` in the .deb), links `$PREFIX/bin/powercontrol`, and installs the icon,
desktop entry and polkit action (`auth_admin_keep`, exec path annotated). `core.helper_command()` picks
how to run the helper: a root-owned launcher (installed copy) is run directly with `pkexec`, so code
running as root cannot be modified by the user. Otherwise (checkout, pip) it runs
`pkexec python3 -c BOOTSTRAP <package parent>`. In an AppImage root cannot read the FUSE mount, so the
package is first copied to a private temp dir. The AppImage ships no polkit policy and the user
authenticates on every Apply.

## Common tasks

**Add a setting.** Add a `Setting` to `core.SETTINGS`. If it can be applied directly, extend
`runtime_values()`, `write_runtime()` and the key tuple in `helper.apply_direct()`. Add values to
`builtin_profiles()`. Add the sysfs files to `tests/fakeroot.py` and update `test_settings_available`.

**Change a built-in profile.** Edit `builtin_profiles()` and `TestProfiles.test_builtins`.

**Change the UI.** Prefer stock libadwaita widgets and style classes, custom CSS only in `widgets.CSS`,
icons must exist in the Adwaita icon theme. GNOME HIG: title case for buttons and headings, sentence
case for descriptions. Render with `tests/run_gui_tests.sh --screenshots /tmp/shots` and look at light
and dark PNGs.

**Release.** Bump `core.VERSION` and push to `main`: CI sees that `docs/screenshots/VERSION` differs and
commits fresh screenshots (or run `scripts/screenshots.sh` yourself). Then tag and push
(`git tag release/1.2.0 && git push origin release/1.2.0`); `release.yml` publishes the tarball, `.deb`, AppImage and
checksums. The .deb targets Ubuntu 24.04+ / Debian 13+ (libadwaita 1.5). Keep its `Depends` in
`build-release.sh` in sync with the README requirements table.

## Commits

Short imperative subject (max 72 chars), optional body explaining why. Example:
`Add per-source Intel GPU frequency limits`.
