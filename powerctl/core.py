"""Hardware access, setting definitions, TLP configuration handling, status and profiles."""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

APP_ID = "com.kenangueler.apps.LaptopPowerControl"
APP_NAME = "Laptop Power Control"
APP_ICON = APP_ID
VERSION = "1.0.0"

# Optional fake filesystem root, used for testing the helper without touching the real system.
ROOT = os.environ.get("POWERCONTROL_SYSROOT", "").rstrip("/")

SYS_CPU = "/sys/devices/system/cpu"
PSTATE = f"{SYS_CPU}/intel_pstate"
CPUFREQ_BOOST = f"{SYS_CPU}/cpufreq/boost"
PLATFORM_PROFILE = "/sys/firmware/acpi/platform_profile"
PLATFORM_CHOICES = "/sys/firmware/acpi/platform_profile_choices"
ASPM_POLICY = "/sys/module/pcie_aspm/parameters/policy"
PSU = "/sys/class/power_supply"
TLP_DEFAULTS = "/usr/share/tlp/defaults.conf"
TLP_DIR = "/etc/tlp.d"
TLP_DROPIN = "/etc/tlp.d/99-powercontrol.conf"
TLP_MAIN = "/etc/tlp.conf"
TLP_BACKUP = "/etc/tlp.conf.powercontrol-backup"
DISABLED_TAG = "#[powercontrol] "
# Root-owned copies made by install.sh (/usr/local) or the .deb package (/usr)
INSTALLED_ENTRIES = ("/usr/lib/laptop-powercontrol/powercontrol.py", "/usr/local/lib/laptop-powercontrol/powercontrol.py")
RESULT_PREFIX = "POWERCONTROL-RESULT "

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "laptop-powercontrol"
)
PROFILES_FILE = os.path.join(CONFIG_DIR, "profiles.json")
PREFS_FILE = os.path.join(CONFIG_DIR, "settings.json")

COLUMNS = ("ac", "bat")
COLUMN_TITLES = {"ac": "Plugged in", "bat": "On battery"}
TLP_SUFFIX = {"ac": "_ON_AC", "bat": "_ON_BAT"}


# --------------------------------------------------------------------------- filesystem
def P(path):
    return ROOT + path


def rd(path, default=None):
    try:
        with open(P(path)) as f:
            return f.read().strip()
    except OSError:
        return default


def rd_int(path, default=None):
    v = rd(path)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def wr(path, value):
    with open(P(path), "w") as f:
        f.write(str(value))


def exists(path):
    return os.path.exists(P(path))


def pglob(pattern):
    n = len(ROOT)
    return sorted(p[n:] for p in glob.glob(P(pattern)))


def cpu_files(name):
    return pglob(f"{SYS_CPU}/cpu[0-9]*/cpufreq/{name}")


def bracketed(text):
    """'[auto] inhibit-charge' -> ('auto', ['auto', 'inhibit-charge'])"""
    if not text:
        return None, []
    items = text.split()
    cur = next((i.strip("[]") for i in items if i.startswith("[")), None)
    return cur, [i.strip("[]") for i in items]


def display_path(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def write_atomic(path, text, mode=0o644):
    real = P(path)
    os.makedirs(os.path.dirname(real), exist_ok=True)
    tmp = real + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, real)


# --------------------------------------------------------------------------- hardware
def battery_name():
    for d in pglob(f"{PSU}/*"):
        if rd(f"{d}/type") == "Battery" and os.path.basename(d).startswith(("BAT", "CMB")):
            return os.path.basename(d)
    return None


def on_ac():
    """Same ranking as TLP: Mains decides, then battery status, then USB supplies."""
    usb_online = False
    bat_discharging = None
    for d in pglob(f"{PSU}/*"):
        t = rd(f"{d}/type")
        if t == "Mains":
            return rd(f"{d}/online") == "1"
        if t == "Battery" and bat_discharging is None:
            bat_discharging = rd(f"{d}/status") == "Discharging"
        if t == "USB" and rd(f"{d}/online") == "1":
            usb_online = True
    if bat_discharging is not None:
        return not bat_discharging
    return usb_online or True


def active_column():
    return "ac" if on_ac() else "bat"


def pstate_active():
    return rd(f"{PSTATE}/status") == "active"


def scaling_driver():
    return rd(f"{SYS_CPU}/cpu0/cpufreq/scaling_driver", "unknown")


def hw_min_perf_pct():
    lo, hi = rd_int(f"{SYS_CPU}/cpu0/cpufreq/cpuinfo_min_freq"), rd_int(f"{SYS_CPU}/cpu0/cpufreq/cpuinfo_max_freq")
    if lo and hi:
        return int(lo * 100 / hi)
    return 0


def charge_thresholds_paths(bat):
    base = f"{PSU}/{bat}"
    for s, e in (("charge_control_start_threshold", "charge_control_end_threshold"),
                 ("charge_start_threshold", "charge_stop_threshold")):
        if exists(f"{base}/{e}"):
            return f"{base}/{s}" if exists(f"{base}/{s}") else None, f"{base}/{e}"
    return None, None


def charge_behaviours():
    bat = battery_name()
    if not bat:
        return None, []
    return bracketed(rd(f"{PSU}/{bat}/charge_behaviour"))


def machine_name():
    vendor = rd("/sys/class/dmi/id/sys_vendor", "")
    version = rd("/sys/class/dmi/id/product_version", "")
    name = rd("/sys/class/dmi/id/product_name", "")
    # Some vendors (e.g. Lenovo) put a SKU in product_name and the readable model in product_version
    model = version if vendor.upper() == "LENOVO" and version else name
    vendor = vendor.capitalize() if vendor.isupper() else vendor
    return " ".join(x for x in (vendor, model) if x) or "Unknown machine"


def cpu_model():
    try:
        with open(P("/proc/cpuinfo")) as f:
            for line in f:
                if line.startswith("model name"):
                    return re.sub(r"\((R|TM)\)", "", line.split(":", 1)[1]).strip()
    except OSError:
        pass
    return "Unknown CPU"


# --------------------------------------------------------------------------- settings
PRETTY = {
    "performance": "Performance", "powersave": "Powersave", "balance_performance": "Balance performance",
    "balance_power": "Balance power", "power": "Power saving", "default": "Default",
    "low-power": "Low power", "balanced": "Balanced", "balanced-performance": "Balanced performance",
    "quiet": "Quiet", "cool": "Cool", "powersupersave": "Power super-save", "schedutil": "Schedutil",
    "ondemand": "On demand", "conservative": "Conservative", "userspace": "Userspace",
    "auto": "Normal", "inhibit-charge": "Inhibit charging", "force-discharge": "Force discharge",
}


def pretty(v):
    return PRETTY.get(v, str(v).replace("_", " ").replace("-", " ").capitalize())


class Setting:
    def __init__(self, key, tlp, title, subtitle, kind, section, *, choices=None, on=None, off=None,
                 labels=None, requires=None, needs_tlp=False, lo=0, hi=100, defaults=None, icon=None):
        self.key, self.tlp, self.title, self.subtitle = key, tlp, title, subtitle
        self.kind, self.section, self._choices = kind, section, choices
        self.on, self.off, self.labels = on, off, labels or {}
        self.requires, self.needs_tlp, self.lo, self.hi = requires, needs_tlp, lo, hi
        self.defaults = defaults or {}
        self.icon = icon

    def choices(self):
        if self.kind == "bool":
            return [self.on, self.off]
        c = self._choices() if callable(self._choices) else self._choices
        return list(c or [])

    def available(self):
        if self.requires and not exists(self.requires):
            return False
        if self.needs_tlp and not tlp_binary():
            return False
        if self.kind == "choice" and not self.choices():
            return False
        return True

    def valid(self, v):
        if v is None:
            return False
        if self.kind == "pct":
            return v.isdigit() and self.lo <= int(v) <= self.hi
        return v in self.choices()

    def label(self, v):
        if v in self.labels:
            return self.labels[v]
        if self.kind == "pct":
            return f"{v} %"
        if self.kind == "bool":
            return "On" if v == self.on else "Off"
        return pretty(v)


def _gov_choices():
    return rd(f"{SYS_CPU}/cpu0/cpufreq/scaling_available_governors", "").split()


def _epp_choices():
    return rd(f"{SYS_CPU}/cpu0/cpufreq/energy_performance_available_preferences", "").split()


def _platform_choices():
    return rd(PLATFORM_CHOICES, "").split()


def _aspm_choices():
    return bracketed(rd(ASPM_POLICY))[1]


SECTIONS = [
    ("Processor", "CPU frequency scaling and performance limits"),
    ("Platform", "Firmware-controlled thermal and fan behaviour"),
    ("Devices", "Peripheral power management (handled by TLP)"),
]

SETTINGS = [
    Setting("governor", "CPU_SCALING_GOVERNOR", "Scaling governor", "Frequency scaling algorithm",
            "choice", "Processor", choices=_gov_choices,
            requires=f"{SYS_CPU}/cpu0/cpufreq/scaling_governor"),
    Setting("epp", "CPU_ENERGY_PERF_POLICY", "Energy/performance policy",
            "Hardware hint trading speed for efficiency", "choice", "Processor", choices=_epp_choices,
            requires=f"{SYS_CPU}/cpu0/cpufreq/energy_performance_preference"),
    Setting("boost", "CPU_BOOST", "Turbo boost", "Allow clocks above base frequency", "bool", "Processor",
            on="1", off="0", requires=None),
    Setting("hwp_dyn_boost", "CPU_HWP_DYN_BOOST", "HWP dynamic boost",
            "Raise minimum clock for I/O-bound tasks", "bool", "Processor", on="1", off="0",
            requires=f"{PSTATE}/hwp_dynamic_boost"),
    Setting("min_perf", "CPU_MIN_PERF", "Minimum performance", "Lower P-state limit (% of max)", "pct",
            "Processor", requires=f"{PSTATE}/min_perf_pct", lo=0, hi=100),
    Setting("max_perf", "CPU_MAX_PERF", "Maximum performance", "Upper P-state limit, lower is cooler",
            "pct", "Processor", requires=f"{PSTATE}/max_perf_pct", lo=1, hi=100),
    Setting("platform", "PLATFORM_PROFILE", "Platform profile", "Firmware thermal, fan and power limits",
            "choice", "Platform", choices=_platform_choices, requires=PLATFORM_PROFILE),
    Setting("wifi_pwr", "WIFI_PWR", "Wi-Fi power saving", "Lets the Wi-Fi adapter doze between packets",
            "bool", "Devices", on="on", off="off", needs_tlp=True,
            defaults={"ac": "off", "bat": "on"}),
    Setting("pcie_aspm", "PCIE_ASPM", "PCIe ASPM", "Active State Power Management for PCIe links",
            "choice", "Devices", choices=_aspm_choices, requires=ASPM_POLICY, needs_tlp=True,
            labels={"default": "Firmware default"}, defaults={"ac": "default", "bat": "default"}),
    Setting("runtime_pm", "RUNTIME_PM", "Runtime power management", "Auto-suspend idle PCI(e) devices",
            "bool", "Devices", on="auto", off="on", needs_tlp=True, defaults={"ac": "on", "bat": "auto"}),
    Setting("sound_pwr", "SOUND_POWER_SAVE", "Audio power saving", "Power off the idle audio codec",
            "choice", "Devices", choices=["0", "1", "10"], needs_tlp=True,
            labels={"0": "Off", "1": "After 1 s", "10": "After 10 s"}, defaults={"ac": "1", "bat": "1"}),
]
SMAP = {s.key: s for s in SETTINGS}


def _boost_available():
    return exists(f"{PSTATE}/no_turbo") or exists(CPUFREQ_BOOST)


SMAP["boost"].available = lambda: _boost_available()  # noqa: E731


def available_settings():
    return [s for s in SETTINGS if s.available()]


def runtime_values():
    v = {
        "governor": rd(f"{SYS_CPU}/cpu0/cpufreq/scaling_governor"),
        "epp": rd(f"{SYS_CPU}/cpu0/cpufreq/energy_performance_preference"),
        "hwp_dyn_boost": rd(f"{PSTATE}/hwp_dynamic_boost"),
        "min_perf": rd(f"{PSTATE}/min_perf_pct"),
        "max_perf": rd(f"{PSTATE}/max_perf_pct"),
        "platform": rd(PLATFORM_PROFILE),
        "pcie_aspm": bracketed(rd(ASPM_POLICY))[0],
    }
    nt = rd(f"{PSTATE}/no_turbo")
    if nt is not None:
        v["boost"] = "0" if nt == "1" else "1"
    else:
        v["boost"] = rd(CPUFREQ_BOOST)
    return {k: x for k, x in v.items() if x is not None}


def write_runtime(key, value, errors):
    """Directly apply one processor/platform setting to sysfs."""
    def w(path, val):
        try:
            wr(path, val)
        except OSError as e:
            errors.append(f"{SMAP[key].title}: cannot write {val} ({e.strerror or e})")
            return False
        return True

    if key == "governor":
        for p in cpu_files("scaling_governor"):
            if not w(p, value):
                break
    elif key == "epp":
        if pstate_active() and rd(f"{SYS_CPU}/cpu0/cpufreq/scaling_governor") == "performance":
            return
        for p in cpu_files("energy_performance_preference"):
            if not w(p, value):
                break
    elif key == "boost":
        if exists(f"{PSTATE}/no_turbo"):
            w(f"{PSTATE}/no_turbo", "0" if value == "1" else "1")
        else:
            w(CPUFREQ_BOOST, value)
    elif key == "hwp_dyn_boost":
        w(f"{PSTATE}/hwp_dynamic_boost", value)
    elif key == "platform":
        w(PLATFORM_PROFILE, value)


def write_perf_range(lo, hi, errors):
    """intel_pstate requires min <= max at every step, so try both orders."""
    for _ in range(2):
        for f, v in (("max_perf_pct", hi), ("min_perf_pct", lo)):
            if v is not None:
                try:
                    wr(f"{PSTATE}/{f}", v)
                except OSError:
                    pass
    if hi is not None and rd(f"{PSTATE}/max_perf_pct") != str(hi):
        errors.append(f"Maximum performance: cannot set {hi} %")


def write_thresholds(start, stop, errors):
    bat = battery_name()
    sp, ep = charge_thresholds_paths(bat) if bat else (None, None)
    if not ep:
        errors.append("Battery charge thresholds are not supported on this machine")
        return
    for _ in range(3):
        for p, v in ((sp, start), (ep, stop)):
            if p and v is not None:
                try:
                    wr(p, v)
                except OSError:
                    pass
    for name, p, v in (("start", sp, start), ("stop", ep, stop)):
        if p and v is not None and rd(p) != str(v):
            errors.append(f"Charge {name} threshold: cannot set {v} % (currently {rd(p)} %)")


# --------------------------------------------------------------------------- TLP
_tlp_cache = {}


def tlp_binary():
    if "bin" not in _tlp_cache:
        if os.environ.get("POWERCONTROL_TLP"):
            _tlp_cache["bin"] = os.environ["POWERCONTROL_TLP"]
        elif ROOT:  # test mode: only the fake tree's tlp, never the host's
            _tlp_cache["bin"] = P("/usr/sbin/tlp") if os.access(P("/usr/sbin/tlp"), os.X_OK) else None
        else:
            _tlp_cache["bin"] = shutil.which("tlp") or ("/usr/sbin/tlp" if os.path.exists("/usr/sbin/tlp") else None)
    return _tlp_cache["bin"]


def tlp_version():
    if "ver" not in _tlp_cache:
        ver = None
        if tlp_binary():
            try:
                out = subprocess.run([tlp_binary(), "--version"], capture_output=True, text=True, timeout=5).stdout
                m = re.search(r"TLP\D*(\d+\.\d+(\.\d+)?)", out)
                ver = m.group(1) if m else None
            except (OSError, subprocess.SubprocessError):
                pass
        _tlp_cache["ver"] = ver
    return _tlp_cache["ver"]


def service_state(name):
    if ROOT:  # test mode: never query the host's systemd
        return "active" if name in rd("/run/powercontrol-test/active-services", "").split() else "inactive"
    try:
        r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


CONF_RE = re.compile(r'^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(?:"([^"]*)"|([^\s#]*))')


def tlp_conf_files():
    files = [TLP_DEFAULTS] + pglob(f"{TLP_DIR}/*.conf") + [TLP_MAIN]
    return [f for f in files if exists(f)]


def parse_conf(path):
    out = []
    try:
        with open(P(path)) as f:
            for n, line in enumerate(f, 1):
                m = CONF_RE.match(line)
                if m:
                    out.append((m.group(1), m.group(2) if m.group(2) is not None else m.group(3), n))
    except OSError:
        pass
    return out


def read_tlp_config():
    """Effective TLP config: {KEY: (value, file, line)} in TLP's precedence order."""
    cfg = {}
    for f in tlp_conf_files():
        for k, v, n in parse_conf(f):
            cfg[k] = (v, f, n)
    return cfg


def managed_keys():
    keys = []
    for s in available_settings():
        keys += [s.tlp + TLP_SUFFIX[c] for c in COLUMNS]
    bat = battery_name()
    if bat and charge_thresholds_paths(bat)[1]:
        keys += [f"START_CHARGE_THRESH_{bat}", f"STOP_CHARGE_THRESH_{bat}"]
    return keys


def find_conflicts():
    """Entries that TLP reads after our drop-in and that would override it."""
    keys = set(managed_keys())
    later = [f for f in pglob(f"{TLP_DIR}/*.conf") if os.path.basename(f) > os.path.basename(TLP_DROPIN)]
    out = []
    for f in later + [TLP_MAIN]:
        for k, v, n in parse_conf(f):
            if k in keys:
                out.append({"key": k, "value": v, "file": f, "line": n})
    return out


def render_dropin(state):
    bat = battery_name()
    lines = [
        f"# Generated by {APP_NAME} {VERSION} on {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "# Manual changes will be overwritten. Use the application to modify these settings.",
    ]
    if state.get("profile"):
        lines.append(f"# Profile: {state['profile']}")
    avail = available_settings()
    for section, _ in SECTIONS:
        items = [s for s in avail if s.section == section]
        if not items:
            continue
        lines += ["", f"# --- {section}"]
        for s in items:
            for c in COLUMNS:
                v = state.get(c, {}).get(s.key)
                if v is not None:
                    lines.append(f'{s.tlp}{TLP_SUFFIX[c]}="{v}"')
    b = state.get("battery") or {}
    if bat and (b.get("start") is not None or b.get("stop") is not None):
        lines += ["", "# --- Battery care"]
        if b.get("start") is not None:
            lines.append(f'START_CHARGE_THRESH_{bat}="{b["start"]}"')
        if b.get("stop") is not None:
            lines.append(f'STOP_CHARGE_THRESH_{bat}="{b["stop"]}"')
    return "\n".join(lines) + "\n"


def load_system_state():
    """Configured values for both columns (TLP config, falling back to live values)."""
    cfg = read_tlp_config()
    rt = runtime_values()
    state = {"ac": {}, "bat": {}, "battery": {}}
    for s in available_settings():
        for c in COLUMNS:
            v = cfg.get(s.tlp + TLP_SUFFIX[c], (None,))[0]
            if not s.valid(v):
                v = rt.get(s.key)
            if not s.valid(v):
                v = s.defaults.get(c)
            if not s.valid(v):
                ch = s.choices()
                v = ch[0] if ch else (str(s.hi) if s.kind == "pct" else None)
            if v is not None:
                state[c][s.key] = v
    bat = battery_name()
    if bat:
        sp, ep = charge_thresholds_paths(bat)
        if ep:
            for k, key, path in (("start", "START", sp), ("stop", "STOP", ep)):
                v = cfg.get(f"{key}_CHARGE_THRESH_{bat}", (None,))[0]
                if not (v and v.isdigit()):
                    v = rd(path) if path else None
                if v and v.isdigit():
                    state["battery"][k] = int(v)
    return state


# --------------------------------------------------------------------------- live status
_prev_stat = None


def cpu_utilisation():
    global _prev_stat
    try:
        with open(P("/proc/stat")) as f:
            vals = [int(x) for x in f.readline().split()[1:]]
    except (OSError, ValueError):
        return None
    idle, total = vals[3] + vals[4], sum(vals[:8])
    prev, _prev_stat = _prev_stat, (idle, total)
    if not prev or total == prev[1]:
        return None
    return 100.0 * (1 - (idle - prev[0]) / (total - prev[1]))


def load_average():
    try:
        return tuple(float(x) for x in rd("/proc/loadavg", "").split()[:3])
    except ValueError:
        return None


def cpu_temperature():
    for h in pglob("/sys/class/hwmon/hwmon*"):
        if rd(f"{h}/name") in ("coretemp", "k10temp", "zenpower"):
            for lab in pglob(f"{h}/temp*_label"):
                if rd(lab) in ("Package id 0", "Tctl", "Tdie"):
                    v = rd_int(lab.replace("_label", "_input"))
                    if v is not None:
                        return v / 1000
            v = rd_int(f"{h}/temp1_input")
            if v is not None:
                return v / 1000
    for z in pglob("/sys/class/thermal/thermal_zone*"):
        if rd(f"{z}/type") in ("x86_pkg_temp", "acpitz"):
            v = rd_int(f"{z}/temp")
            if v is not None:
                return v / 1000
    return None


def fan_speed():
    for f in pglob("/sys/class/hwmon/hwmon*/fan*_input"):
        v = rd_int(f)
        if v is not None:
            return v
    return None


def fmt_duration(hours):
    if hours is None or hours <= 0 or hours > 72:
        return None
    m = int(round(hours * 60))
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def collect_status():
    st = {"on_ac": on_ac(), "runtime": runtime_values()}
    freqs = [rd_int(p, 0) for p in cpu_files("scaling_cur_freq")]
    freqs = [f for f in freqs if f]
    st["freq_avg"] = sum(freqs) / len(freqs) / 1000 if freqs else None
    st["freq_max"] = max(freqs) / 1000 if freqs else None
    st["cpus"] = len(freqs)
    st["load"] = cpu_utilisation()
    st["loadavg"] = load_average()
    st["temp"] = cpu_temperature()
    st["fan"] = fan_speed()
    bat = battery_name()
    st["battery"] = None
    if bat:
        base = f"{PSU}/{bat}"
        volt = rd_int(f"{base}/voltage_now")
        power = rd_int(f"{base}/power_now")
        if power is None and volt and rd_int(f"{base}/current_now") is not None:
            power = rd_int(f"{base}/current_now") * volt / 1e6
        e_now, e_full, e_design = (rd_int(f"{base}/energy_{x}") for x in ("now", "full", "full_design"))
        if e_now is None and volt:
            c = [rd_int(f"{base}/charge_{x}") for x in ("now", "full", "full_design")]
            if None not in c:
                e_now, e_full, e_design = (x * volt / 1e6 for x in c)
        sp, ep = charge_thresholds_paths(bat)
        b = {
            "name": bat,
            "capacity": rd_int(f"{base}/capacity"),
            "status": rd(f"{base}/status", "Unknown"),
            "power": power / 1e6 if power is not None else None,
            "energy_now": e_now / 1e6 if e_now else None,
            "energy_full": e_full / 1e6 if e_full else None,
            "energy_design": e_design / 1e6 if e_design else None,
            "voltage": volt / 1e6 if volt else None,
            "cycles": rd_int(f"{base}/cycle_count"),
            "manufacturer": rd(f"{base}/manufacturer"),
            "model": rd(f"{base}/model_name"),
            "technology": rd(f"{base}/technology"),
            "start": rd_int(sp) if sp else None,
            "stop": rd_int(ep) if ep else None,
            "behaviour": bracketed(rd(f"{base}/charge_behaviour"))[0],
        }
        b["health"] = 100 * b["energy_full"] / b["energy_design"] if b["energy_full"] and b["energy_design"] else None
        b["time"] = None
        if b["power"] and b["power"] > 0.3 and b["energy_now"]:
            if b["status"] == "Discharging":
                b["time"] = ("remaining", b["energy_now"] / b["power"])
            elif b["status"] == "Charging" and b["energy_full"]:
                target = b["energy_full"] * (b["stop"] or 100) / 100
                if target > b["energy_now"]:
                    b["time"] = ("until full", (target - b["energy_now"]) / b["power"])
        st["battery"] = b
    return st


# --------------------------------------------------------------------------- profiles & prefs
def _pick(key, *prefs):
    s = SMAP.get(key)
    if not s or not s.available():
        return None
    if s.kind == "pct":
        return prefs[0]
    ch = s.choices()
    return next((p for p in prefs if p in ch), None)


def _mk(**kw):
    return {k: _pick(k, *(v if isinstance(v, tuple) else (v,))) for k, v in kw.items()
            if _pick(k, *(v if isinstance(v, tuple) else (v,))) is not None}


def builtin_profiles():
    bal_ac = _mk(governor="powersave", epp="balance_performance", boost="1", hwp_dyn_boost="1",
                 min_perf="0", max_perf="100", platform=("balanced", "balanced-performance"),
                 wifi_pwr="off", pcie_aspm="default", runtime_pm="on", sound_pwr="1")
    bal_bat = _mk(governor="powersave", epp="balance_power", boost="1", hwp_dyn_boost="0",
                  min_perf="0", max_perf="100", platform="balanced",
                  wifi_pwr="on", pcie_aspm="default", runtime_pm="auto", sound_pwr="1")
    perf = _mk(governor=("performance", "powersave"), epp="performance", boost="1", hwp_dyn_boost="1",
               min_perf="0", max_perf="100", platform=("performance", "balanced-performance"),
               wifi_pwr="off", pcie_aspm=("performance", "default"), runtime_pm="on", sound_pwr="0")
    maximum = _mk(governor=("performance", "powersave"), epp="performance", boost="1", hwp_dyn_boost="1",
                  min_perf="100", max_perf="100", platform=("performance", "balanced-performance"),
                  wifi_pwr="off", pcie_aspm=("performance", "default"), runtime_pm="on", sound_pwr="0")
    saver = _mk(governor="powersave", epp=("power", "balance_power"), boost="0", hwp_dyn_boost="0",
                min_perf="0", max_perf="60", platform=("low-power", "quiet", "cool"),
                wifi_pwr="on", pcie_aspm=("powersupersave", "powersave"), runtime_pm="auto", sound_pwr="1")
    quiet = _mk(governor="powersave", epp=("balance_power", "power"), boost="0", hwp_dyn_boost="0",
                min_perf="0", max_perf="70", platform=("quiet", "low-power", "cool"),
                wifi_pwr="off", pcie_aspm="default", runtime_pm="auto", sound_pwr="1")
    quiet_bat = dict(quiet, wifi_pwr="on") if "wifi_pwr" in quiet else dict(quiet)
    return {
        "Balanced": {"description": "Responsive on AC, efficient on battery. Recommended for daily use.",
                     "icon": "power-profile-balanced-symbolic", "ac": bal_ac, "bat": bal_bat},
        "Performance": {"description": "Maximum speed on both power sources. Louder and warmer.",
                        "icon": "power-profile-performance-symbolic", "ac": perf, "bat": dict(perf)},
        "Maximum": {"description": "Everything at full power: CPU pinned at 100 %, turbo, performance firmware "
                                   "profile, all device power saving off. Hot, loud, short battery life.",
                    "icon": "thunderbolt-symbolic", "ac": maximum, "bat": dict(maximum)},
        "Battery Saver": {"description": "Balanced when plugged in, maximum runtime on battery.",
                          "icon": "power-profile-power-saver-symbolic", "ac": dict(bal_ac), "bat": saver},
        "Quiet": {"description": "No turbo and a capped clock for cool, near-silent operation.",
                  "icon": "weather-windy-symbolic", "ac": quiet, "bat": quiet_bat},
    }


def _load_json(path, default):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def clean_columns(data):
    """Keep only known keys with valid values."""
    out = {}
    for c in COLUMNS:
        out[c] = {}
        for k, v in (data.get(c) or {}).items():
            s = SMAP.get(k)
            if s and s.available() and s.valid(str(v)):
                out[c][k] = str(v)
    return out


HISTORY_DEPTH = 15


def _now():
    return time.strftime("%Y-%m-%d %H:%M")


def _version(entry):
    return {"description": entry.get("description", ""), "ac": dict(entry.get("ac") or {}),
            "bat": dict(entry.get("bat") or {}), "modified": entry.get("modified", "")}


def _same(a, b):
    return a.get("ac") == b.get("ac") and a.get("bat") == b.get("bat") and \
        a.get("description", "") == b.get("description", "")


class ProfileStore:
    """Built-in profiles (optionally overridden by the user) and custom profiles.

    Every save keeps the previous version in a history list and every custom profile remembers the
    version it was created with, so profiles can be reverted step by step or reset to their original.
    """

    def __init__(self):
        self.builtin = builtin_profiles()
        data = _load_json(PROFILES_FILE, {})
        self.custom = data.get("profiles", {}) if isinstance(data.get("profiles"), dict) else {}
        ov = data.get("builtin_overrides", {})
        self.overrides = {k: v for k, v in ov.items() if k in self.builtin} if isinstance(ov, dict) else {}

    def save(self):
        _save_json(PROFILES_FILE, {"version": 2, "profiles": self.custom, "builtin_overrides": self.overrides})

    # ---- queries
    def names(self):
        return list(self.builtin) + sorted(self.custom, key=str.lower)

    def is_builtin(self, name):
        return name in self.builtin

    def _entry(self, name):
        if name in self.builtin:
            return self.overrides.get(name) or self.builtin[name]
        return self.custom.get(name)

    def get(self, name):
        p = self._entry(name)
        if not p:
            return None
        res = clean_columns(p)
        res["description"] = p.get("description", "")
        res["icon"] = (self.builtin.get(name) or p).get("icon", "starred-symbolic")
        res["modified"] = p.get("modified", "")
        return res

    def is_customized(self, name):
        """Built-in profile changed by the user."""
        return name in self.overrides

    def history(self, name):
        e = self.overrides.get(name) if name in self.builtin else self.custom.get(name)
        return list((e or {}).get("history", []))

    def can_revert(self, name):
        return bool(self.history(name))

    def can_reset(self, name):
        if name in self.builtin:
            return name in self.overrides
        e = self.custom.get(name)
        return bool(e and e.get("original") and not _same(e, e["original"]))

    # ---- modifications
    def put(self, name, ac, bat, description=""):
        new = {"description": description, "ac": dict(ac), "bat": dict(bat), "modified": _now()}
        if name in self.builtin:
            old = self._entry(name)
            if _same(new, self.builtin[name]) and name in self.overrides:
                self.overrides.pop(name)  # identical to the default again
            elif not _same(new, old):
                hist = (self.overrides.get(name) or {}).get("history", [])
                hist = ([_version(old)] + hist)[:HISTORY_DEPTH]
                self.overrides[name] = dict(new, history=hist)
        else:
            old = self.custom.get(name)
            if old:
                if _same(new, old):
                    return
                hist = ([_version(old)] + old.get("history", []))[:HISTORY_DEPTH]
                self.custom[name] = dict(new, history=hist, original=old.get("original") or _version(old),
                                         created=old.get("created", ""))
            else:
                self.custom[name] = dict(new, history=[], original=_version(new), created=_now())
        self.save()

    def revert(self, name):
        """Go back to the previously saved version."""
        e = self.overrides.get(name) if name in self.builtin else self.custom.get(name)
        if not e or not e.get("history"):
            return False
        prev, rest = e["history"][0], e["history"][1:]
        e.update(_version(prev))
        e["history"] = rest
        if name in self.builtin and _same(e, self.builtin[name]):
            self.overrides.pop(name)
        self.save()
        return True

    def reset(self, name):
        """Built-in: restore factory values. Custom: restore the version it was created with."""
        if name in self.builtin:
            if self.overrides.pop(name, None) is None:
                return False
        else:
            e = self.custom.get(name)
            if not e or not e.get("original"):
                return False
            hist = ([_version(e)] + e.get("history", []))[:HISTORY_DEPTH]
            e.update(_version(e["original"]))
            e["modified"] = _now()
            e["history"] = hist
        self.save()
        return True

    def delete(self, name):
        self.custom.pop(name, None)
        self.save()

    def rename(self, old, new):
        if old in self.custom and new not in self.builtin and new not in self.custom:
            self.custom[new] = self.custom.pop(old)
            self.save()

    def snapshot(self):
        return json.loads(json.dumps({"custom": self.custom, "overrides": self.overrides}))

    def restore(self, snap):
        self.custom = snap["custom"]
        self.overrides = snap["overrides"]
        self.save()

    def reset_all(self):
        """Delete all custom profiles and built-in overrides. Returns the path of the backup file."""
        backup = None
        if os.path.exists(PROFILES_FILE) and (self.custom or self.overrides):
            backup = f"{PROFILES_FILE}.{time.strftime('%Y%m%d-%H%M%S')}.bak"
            shutil.copy2(PROFILES_FILE, backup)
        self.custom, self.overrides = {}, {}
        self.save()
        return backup

    # ---- files
    def export(self, name, path):
        p = self.get(name)
        with open(path, "w") as f:
            json.dump({"name": name, "description": p["description"], "ac": p["ac"], "bat": p["bat"],
                       "app": APP_NAME, "version": VERSION}, f, indent=2)

    def import_file(self, path):
        with open(path) as f:
            data = json.load(f)
        name = str(data.get("name") or os.path.splitext(os.path.basename(path))[0]).strip()[:60]
        cols = clean_columns(data)
        if not cols["ac"] and not cols["bat"]:
            raise ValueError("file does not contain any usable settings")
        base, i = name, 2
        while name in self.builtin or name in self.custom:
            name = f"{base} ({i})"
            i += 1
        self.put(name, cols["ac"], cols["bat"], str(data.get("description", "")))
        return name


def load_prefs():
    p = {"persist": True, "fix_conflicts": True, "active_profile": None}
    p.update(_load_json(PREFS_FILE, {}))
    return p


def save_prefs(prefs):
    _save_json(PREFS_FILE, prefs)


# --------------------------------------------------------------------------- talking to the helper
PKG_DIR = os.path.dirname(os.path.abspath(__file__))
# Used when there is no trusted launcher: put the package's parent dir on sys.path and run the CLI.
BOOTSTRAP = "import sys; sys.path.insert(0, sys.argv.pop(1)); from powerctl.cli import main; sys.exit(main())"


def _root_owned(path):
    """True if path and its directory are owned by root and not writable by anyone else."""
    try:
        for p in (path, os.path.dirname(path)):
            st = os.stat(p)
            if st.st_uid != 0 or st.st_mode & 0o022:
                return False
        return True
    except OSError:
        return False


def helper_command():
    """Command prefix for the privileged helper. Returns (argv, temp_dir_to_remove_or_None)."""
    launcher = os.path.join(os.path.dirname(PKG_DIR), "powercontrol.py")
    if ROOT:  # test mode: no privilege escalation
        return [sys.executable, "-c", BOOTSTRAP, os.path.dirname(PKG_DIR)], None
    if _root_owned(launcher):
        return ["pkexec", launcher], None
    for entry in INSTALLED_ENTRIES:
        if _root_owned(entry):
            return ["pkexec", entry], None
    if os.environ.get("APPIMAGE"):
        # root cannot read the user's FUSE mount, so hand it a private copy of the package
        tmp = tempfile.mkdtemp(prefix="powercontrol-")
        shutil.copytree(PKG_DIR, os.path.join(tmp, "powerctl"), ignore=shutil.ignore_patterns("__pycache__"))
        return ["pkexec", sys.executable, "-c", BOOTSTRAP, tmp], tmp
    # source checkout or pip install: user-owned code, authenticated on every call
    return ["pkexec", sys.executable, "-c", BOOTSTRAP, os.path.dirname(PKG_DIR)], None


def build_apply_args(state, persist, fix_conflicts):
    args = []
    for c in COLUMNS:
        args += [f"{c}.{k}={v}" for k, v in sorted(state.get(c, {}).items())]
    for k in ("start", "stop"):
        v = (state.get("battery") or {}).get(k)
        if v is not None:
            args.append(f"{k}={int(v)}")
    if state.get("profile"):
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", state["profile"])[:40]
        args.append(f"profile={safe}")
    if persist:
        args.append("--persist")
    if fix_conflicts:
        args.append("--fix-conflicts")
    return args


def run_helper(args, action="--apply"):
    prefix, tmp = helper_command()
    cmd = prefix + [action, *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except FileNotFoundError as e:
        return {"ok": False, "errors": [f"Cannot run {e.filename}: not installed"], "warnings": [], "notes": []}
    except subprocess.TimeoutExpired:
        return {"ok": False, "errors": ["The privileged helper timed out"], "warnings": [], "notes": []}
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    for line in reversed(r.stdout.splitlines()):
        if line.startswith(RESULT_PREFIX):
            try:
                return json.loads(line[len(RESULT_PREFIX):])
            except ValueError:
                break
    if r.returncode in (126, 127) and cmd[0] == "pkexec":
        return {"ok": False, "cancelled": True, "errors": [], "warnings": [], "notes": []}
    msg = (r.stderr or r.stdout).strip() or f"helper exited with status {r.returncode}"
    return {"ok": False, "errors": [msg], "warnings": [], "notes": []}
