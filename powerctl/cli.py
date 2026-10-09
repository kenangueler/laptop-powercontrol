"""Command line interface and dispatch (GUI, CLI commands, privileged helper).

Laptop Power Control: per power source CPU, platform, device and battery tuning.

  powercontrol                        start the GUI
  powercontrol --status               print live status
  powercontrol --list-profiles        list profiles (* marks the active one)
  powercontrol --profile NAME         apply a profile (asks for authentication)
  powercontrol --revert-profile NAME  go back to the previously saved version of a profile
  powercontrol --reset-profile NAME   reset a profile (built-in: defaults, custom: original version)
  powercontrol --reset-all-profiles   delete custom profiles and reset built-ins (a backup is kept)
  powercontrol --version              print the version
  powercontrol --apply ARGS...        privileged helper (internal, run through pkexec)
  powercontrol --remove-config        privileged helper: remove the generated TLP configuration
"""
import sys

USAGE = __doc__.split("\n\n", 1)[1].strip()


def cli_status():
    from . import core
    st = core.collect_status()
    b = st["battery"]
    rt = st["runtime"]
    print(f"{core.APP_NAME} {core.VERSION} - {core.machine_name()}")
    print(f"Power source : {'AC' if st['on_ac'] else 'Battery'}")
    if b:
        t = f", {core.fmt_duration(b['time'][1])} {b['time'][0]}" if b["time"] else ""
        print(f"Battery      : {b['capacity']} % ({b['status']}{t}), {b['power'] or 0:.1f} W")
        if b["stop"] is not None:
            print(f"Thresholds   : {b['start']} - {b['stop']} %")
        if b["health"]:
            print(f"Health       : {b['health']:.1f} %, {b['cycles']} cycles")
    if st["freq_avg"]:
        print(f"CPU          : {st['freq_avg']:.0f} MHz avg"
              + (f", {st['temp']:.0f} °C" if st["temp"] is not None else "")
              + (f", fan {st['fan']} RPM" if st["fan"] is not None else ""))
    for k, v in rt.items():
        print(f"  {core.SMAP[k].title:<26} {core.SMAP[k].label(v)}")
    prefs = core.load_prefs()
    print(f"Profile      : {prefs.get('active_profile') or 'custom'}")
    c = core.find_conflicts()
    if c:
        print(f"Warning      : {len(c)} entries in TLP config override this app (fixed on next apply)")
    return 0


def cli_list():
    from . import core
    store = core.ProfileStore()
    active = core.load_prefs().get("active_profile")
    for n in store.names():
        p = store.get(n)
        tag = ("built-in, customized" if store.is_customized(n) else "built-in") if store.is_builtin(n) \
            else "custom"
        print(f"{'*' if n == active else ' '} {n:<24} [{tag}] {p['description']}")
    return 0


def cli_profile(name):
    from . import core
    store = core.ProfileStore()
    p = store.get(name)
    if not p:
        print(f"Unknown profile: {name}. Use --list-profiles.", file=sys.stderr)
        return 2
    sysst = core.load_system_state()
    state = {"ac": {**sysst["ac"], **p["ac"]}, "bat": {**sysst["bat"], **p["bat"]},
             "battery": sysst["battery"], "profile": name}
    prefs = core.load_prefs()
    persist = prefs["persist"] and bool(core.tlp_binary())
    res = core.run_helper(core.build_apply_args(state, persist, persist and prefs["fix_conflicts"]))
    if res.get("cancelled"):
        print("Authentication cancelled", file=sys.stderr)
        return 126
    for n in res["notes"]:
        print(n)
    for w in res["warnings"]:
        print(f"warning: {w}", file=sys.stderr)
    for e in res["errors"]:
        print(f"error: {e}", file=sys.stderr)
    if res["ok"]:
        prefs["active_profile"] = name
        core.save_prefs(prefs)
        print(f"Profile '{name}' applied.")
    return 0 if res["ok"] else 1


def cli_profile_op(op, name):
    from . import core
    store = core.ProfileStore()
    if op == "reset-all":
        backup = store.reset_all()
        print("All profiles reset." + (f" Backup: {backup}" if backup else ""))
        return 0
    if not store.get(name):
        print(f"Unknown profile: {name}. Use --list-profiles.", file=sys.stderr)
        return 2
    ok = store.revert(name) if op == "revert" else store.reset(name)
    if not ok:
        print(f"Nothing to {op} for '{name}'.", file=sys.stderr)
        return 1
    print(f"Profile '{name}' {'reverted to its previous version' if op == 'revert' else 'reset'}. "
          f"Apply it with --profile to activate.")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("--apply", "--remove-config"):
        from . import helper
        return helper.main(argv[0], argv[1:])
    if argv and argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0
    if argv and argv[0] == "--status":
        return cli_status()
    if argv and argv[0] == "--list-profiles":
        return cli_list()
    if argv and argv[0] == "--profile":
        if len(argv) < 2:
            print("--profile needs a name", file=sys.stderr)
            return 2
        return cli_profile(" ".join(argv[1:]))
    if argv and argv[0] in ("--revert-profile", "--reset-profile"):
        if len(argv) < 2:
            print(f"{argv[0]} needs a name", file=sys.stderr)
            return 2
        return cli_profile_op(argv[0][2:].split("-")[0], " ".join(argv[1:]))
    if argv and argv[0] == "--reset-all-profiles":
        return cli_profile_op("reset-all", None)
    if argv and argv[0] == "--version":
        from . import core
        print(core.VERSION)
        return 0
    if argv:
        print(f"Unknown option: {argv[0]}\n\n{USAGE}", file=sys.stderr)
        return 2
    from . import gui
    return gui.main()
