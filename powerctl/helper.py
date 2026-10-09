"""Privileged helper, executed as root through pkexec. Validates every argument strictly."""
import json
import os
import re
import shutil
import subprocess

from . import core
from .core import COLUMNS, SMAP

VALUE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


def _result():
    return {"ok": True, "errors": [], "warnings": [], "notes": [], "column": None}


def _emit(res):
    res["ok"] = not res["errors"]
    print(core.RESULT_PREFIX + json.dumps(res), flush=True)
    return 0 if res["ok"] else 1


def parse(argv):
    req = {"ac": {}, "bat": {}, "battery": {}, "charge_behaviour": None, "profile": None,
           "persist": False, "fix_conflicts": False}
    errors = []
    avail = {s.key: s for s in core.available_settings()}
    for a in argv:
        if a == "--persist":
            req["persist"] = True
        elif a == "--fix-conflicts":
            req["fix_conflicts"] = True
        elif "=" in a:
            k, v = a.split("=", 1)
            if not VALUE_RE.match(v):
                errors.append(f"Rejected value: {a!r}")
            elif k.startswith(("ac.", "bat.")):
                col, key = k.split(".", 1)
                s = avail.get(key)
                if not s:
                    errors.append(f"Unsupported setting: {key}")
                elif not s.valid(v):
                    errors.append(f"Invalid value for {s.title}: {v}")
                else:
                    req[col][key] = v
            elif k in ("start", "stop"):
                lo, hi = (0, 99) if k == "start" else (1, 100)
                if not v.isdigit() or not lo <= int(v) <= hi:
                    errors.append(f"Invalid charge {k} threshold: {v}")
                else:
                    req["battery"][k] = int(v)
            elif k == "charge_behaviour":
                if v not in core.charge_behaviours()[1]:
                    errors.append(f"Unsupported charge behaviour: {v}")
                else:
                    req["charge_behaviour"] = v
            elif k == "profile":
                req["profile"] = v
            else:
                errors.append(f"Unknown option: {k}")
        else:
            errors.append(f"Unknown argument: {a!r}")
    b = req["battery"]
    if "start" in b and "stop" in b and b["start"] >= b["stop"]:
        errors.append("Charge start threshold must be lower than the stop threshold")
    for c in COLUMNS:
        lo, hi = req[c].get("min_perf"), req[c].get("max_perf")
        if lo and hi and int(lo) > int(hi):
            errors.append(f"{core.COLUMN_TITLES[c]}: minimum performance exceeds maximum")
    return req, errors


def disable_conflicts(res):
    conflicts = core.find_conflicts()
    by_file = {}
    for c in conflicts:
        by_file.setdefault(c["file"], set()).add(c["line"])
    for f, lines in by_file.items():
        real = core.P(f)
        with open(real) as fh:
            content = fh.readlines()
        if f == core.TLP_MAIN and not core.exists(core.TLP_BACKUP):
            shutil.copy2(real, core.P(core.TLP_BACKUP))
            res["notes"].append(f"Backup of {f} saved as {core.TLP_BACKUP}")
        for n in lines:
            content[n - 1] = core.DISABLED_TAG + content[n - 1]
        core.write_atomic(f, "".join(content), os.stat(real).st_mode & 0o777)
    if conflicts:
        res["notes"].append(f"Disabled {len(conflicts)} overriding entr{'y' if len(conflicts) == 1 else 'ies'} "
                            f"in {', '.join(sorted(by_file))}")


def reenable_conflicts(res):
    files = [core.TLP_MAIN] + core.pglob(f"{core.TLP_DIR}/*.conf")
    total = 0
    for f in files:
        if not core.exists(f):
            continue
        real = core.P(f)
        with open(real) as fh:
            content = fh.readlines()
        n = sum(1 for ln in content if ln.startswith(core.DISABLED_TAG))
        if n:
            content = [ln[len(core.DISABLED_TAG):] if ln.startswith(core.DISABLED_TAG) else ln for ln in content]
            core.write_atomic(f, "".join(content), os.stat(real).st_mode & 0o777)
            total += n
    if total:
        res["notes"].append(f"Re-enabled {total} entr{'y' if total == 1 else 'ies'} previously disabled by this app")


def run_tlp(res):
    tlp = core.tlp_binary()
    try:
        r = subprocess.run([tlp, "start"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        res["errors"].append(f"Running 'tlp start' failed: {e}")
        return
    out = "\n".join(x for x in (r.stdout.strip(), r.stderr.strip()) if x)
    if r.returncode != 0:
        res["warnings"].append(f"'tlp start' exited with status {r.returncode}: {out}")
    elif re.search(r"(?im)^(error|warning)", out):
        res["warnings"].append(f"TLP: {out}")


def apply_direct(col_values, battery, res):
    errs = []
    for key in ("governor", "epp", "platform", "boost", "hwp_dyn_boost"):
        if key in col_values:
            core.write_runtime(key, col_values[key], errs)
    if "min_perf" in col_values or "max_perf" in col_values:
        core.write_perf_range(col_values.get("min_perf"), col_values.get("max_perf"), errs)
    if battery:
        core.write_thresholds(battery.get("start"), battery.get("stop"), errs)
    res["errors"] += errs


def verify(col, values, battery, res):
    rt = core.runtime_values()
    title = core.COLUMN_TITLES[col]
    perf_gov = core.pstate_active() and rt.get("governor") == "performance"
    for key, want in values.items():
        if key not in rt:
            continue
        have = rt[key]
        s = SMAP[key]
        if key == "epp" and perf_gov:
            continue
        if key == "min_perf":
            if int(have) == max(int(want), core.hw_min_perf_pct()) or int(have) == int(want):
                continue
            if int(want) < int(have) <= core.hw_min_perf_pct() + 1:
                continue
        if have != want:
            res["warnings"].append(f"{title}: {s.title} is {s.label(have)}, expected {s.label(want)}")
    bat = core.battery_name()
    if battery and bat:
        sp, ep = core.charge_thresholds_paths(bat)
        for k, p in (("start", sp), ("stop", ep)):
            if p and k in battery and core.rd_int(p) != battery[k]:
                res["warnings"].append(f"Charge {k} threshold is {core.rd(p)} %, expected {battery[k]} %")


def apply_main(argv):
    res = _result()
    req, errors = parse(argv)
    if errors:
        res["errors"] = errors
        return _emit(res)

    col = core.active_column()
    res["column"] = col
    has_settings = bool(req["ac"] or req["bat"] or req["battery"])
    tlp = core.tlp_binary()

    if has_settings:
        if req["persist"] and tlp:
            try:
                core.write_atomic(core.TLP_DROPIN, core.render_dropin(req))
                res["notes"].append(f"Configuration written to {core.TLP_DROPIN}")
                if req["fix_conflicts"]:
                    disable_conflicts(res)
                else:
                    for c in core.find_conflicts():
                        res["warnings"].append(f"{c['key']} in {c['file']}:{c['line']} overrides this setting")
            except OSError as e:
                res["errors"].append(f"Cannot write TLP configuration: {e}")
                return _emit(res)
            run_tlp(res)
            # Apply live values directly too, TLP skips some writes (e.g. unchanged thresholds).
            apply_direct(req[col], req["battery"], res)
        else:
            apply_direct(req[col], req["battery"], res)
            if req["persist"] and not tlp:
                res["warnings"].append("TLP is not installed: settings were applied for this session only")
            else:
                res["notes"].append("Applied for this session only; not saved to the TLP configuration")
        verify(col, req[col], req["battery"], res)

    if req["charge_behaviour"]:
        bat = core.battery_name()
        try:
            core.wr(f"{core.PSU}/{bat}/charge_behaviour", req["charge_behaviour"])
        except OSError as e:
            res["errors"].append(f"Cannot set charge behaviour: {e.strerror or e}")
        else:
            cur = core.charge_behaviours()[0] or core.rd(f"{core.PSU}/{bat}/charge_behaviour")
            if cur != req["charge_behaviour"]:
                res["warnings"].append(f"Charge behaviour is {cur}, expected {req['charge_behaviour']}")
    return _emit(res)


def remove_main(argv):
    res = _result()
    if argv:
        res["errors"].append("--remove-config takes no arguments")
        return _emit(res)
    try:
        if core.exists(core.TLP_DROPIN):
            os.remove(core.P(core.TLP_DROPIN))
            res["notes"].append(f"Removed {core.TLP_DROPIN}")
        reenable_conflicts(res)
    except OSError as e:
        res["errors"].append(str(e))
        return _emit(res)
    if core.tlp_binary():
        run_tlp(res)
    return _emit(res)


def main(action, argv):
    if os.geteuid() != 0 and not core.ROOT:
        res = _result()
        res["errors"].append("The helper must run as root (use pkexec)")
        return _emit(res)
    if action == "--remove-config":
        return remove_main(argv)
    return apply_main(argv)
