"""Build a synthetic sysfs + TLP tree (generic Intel laptop, intel_pstate, 8 CPUs) for tests.

The application reads every system path through ``core.P()``, which prefixes ``POWERCONTROL_SYSROOT``.
Pointing that variable at a tree built here lets the GUI and the privileged helper run without root and
without touching the real machine.

    python3 tests/fakeroot.py /tmp/fakeroot      # build a tree manually
"""
import os
import shutil
import sys

CPUS = 8

TLP_CONF = """\
# /etc/tlp.conf (test fixture) - entries below override drop-ins in /etc/tlp.d
TLP_ENABLE=1
CPU_SCALING_GOVERNOR_ON_AC=powersave
#CPU_SCALING_GOVERNOR_ON_BAT=powersave
CPU_ENERGY_PERF_POLICY_ON_AC=performance
CPU_ENERGY_PERF_POLICY_ON_BAT=power
CPU_MAX_PERF_ON_BAT=30
CPU_BOOST_ON_AC=1
CPU_BOOST_ON_BAT=0
PLATFORM_PROFILE_ON_AC=performance
PLATFORM_PROFILE_ON_BAT=low-power
WIFI_PWR_ON_AC=off
WIFI_PWR_ON_BAT=on
PCIE_ASPM_ON_BAT=powersupersave
START_CHARGE_THRESH_BAT0=70
STOP_CHARGE_THRESH_BAT0=80
"""

DEFAULTS_CONF = """\
TLP_ENABLE="1"
TLP_PERSISTENT_DEFAULT="0"
WIFI_PWR_ON_AC="off"
WIFI_PWR_ON_BAT="on"
PCIE_ASPM_ON_AC="default"
RUNTIME_PM_ON_AC="on"
RUNTIME_PM_ON_BAT="auto"
SOUND_POWER_SAVE_ON_AC="1"
SOUND_POWER_SAVE_ON_BAT="1"
"""


def _w(root, path, value):
    full = root + path
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(f"{value}\n")


def build(root, on_ac=True):
    """(Re)create the fake tree at ``root``."""
    if os.path.isdir(root):
        shutil.rmtree(root)
    os.makedirs(root)
    cpu = "/sys/devices/system/cpu"
    for i in range(CPUS):
        d = f"{cpu}/cpu{i}/cpufreq"
        _w(root, f"{d}/scaling_governor", "powersave")
        _w(root, f"{d}/scaling_available_governors", "performance powersave")
        _w(root, f"{d}/energy_performance_preference", "balance_performance")
        _w(root, f"{d}/energy_performance_available_preferences",
           "default performance balance_performance balance_power power")
        _w(root, f"{d}/scaling_cur_freq", 1800000 + i * 100000)
        _w(root, f"{d}/scaling_driver", "intel_pstate")
        _w(root, f"{d}/cpuinfo_min_freq", 400000)
        _w(root, f"{d}/cpuinfo_max_freq", 4900000)
    for name, value in (("status", "active"), ("no_turbo", 0), ("min_perf_pct", 8), ("max_perf_pct", 100),
                        ("hwp_dynamic_boost", 0)):
        _w(root, f"{cpu}/intel_pstate/{name}", value)
    _w(root, "/sys/firmware/acpi/platform_profile", "balanced")
    _w(root, "/sys/firmware/acpi/platform_profile_choices", "low-power balanced performance")
    _w(root, "/sys/module/pcie_aspm/parameters/policy", "[default] performance powersave powersupersave")

    psu = "/sys/class/power_supply"
    _w(root, f"{psu}/AC/type", "Mains")
    _w(root, f"{psu}/AC/online", 1 if on_ac else 0)
    bat = {
        "type": "Battery", "status": "Not charging" if on_ac else "Discharging", "capacity": 79,
        "power_now": 0 if on_ac else 7500000, "energy_now": 41410000, "energy_full": 52210000,
        "energy_full_design": 52500000, "voltage_now": 16723000, "cycle_count": 27,
        "manufacturer": "Example", "model_name": "BAT-TEST-01", "technology": "Li-poly",
        "charge_control_start_threshold": 70, "charge_control_end_threshold": 80,
        "charge_behaviour": "[auto] inhibit-charge force-discharge",
    }
    for k, v in bat.items():
        _w(root, f"{psu}/BAT0/{k}", v)
    _w(root, f"{psu}/ucsi-source-psy-USBC000:001/type", "USB")
    _w(root, f"{psu}/ucsi-source-psy-USBC000:001/online", 0)

    _w(root, "/sys/class/hwmon/hwmon0/name", "coretemp")
    _w(root, "/sys/class/hwmon/hwmon0/temp1_label", "Package id 0")
    _w(root, "/sys/class/hwmon/hwmon0/temp1_input", 51000)
    _w(root, "/sys/class/hwmon/hwmon1/name", "example_fan")
    _w(root, "/sys/class/hwmon/hwmon1/fan1_input", 1990)

    _w(root, "/sys/class/dmi/id/sys_vendor", "Example")
    _w(root, "/sys/class/dmi/id/product_name", "Laptop 14")

    cpuinfo = "".join(f"processor\t: {i}\nmodel name\t: Example Mobile CPU 8-Core\n\n" for i in range(CPUS))
    _w(root, "/proc/cpuinfo", cpuinfo.rstrip("\n"))
    write_proc_stat(root, 0)
    _w(root, "/proc/loadavg", "0.85 0.72 0.64 2/512 4242")

    _w(root, "/run/powercontrol-test/active-services", "tlp")
    tlp = root + "/usr/sbin/tlp"  # stand-in for the tlp binary: reports a version, accepts 'start'
    _w(root, "/usr/sbin/tlp", '#!/bin/sh\n[ "$1" = "--version" ] && echo "TLP 1.8.0"\nexit 0')
    os.chmod(tlp, 0o755)

    _w(root, "/etc/tlp.conf", TLP_CONF.rstrip("\n"))
    _w(root, "/etc/tlp.d/00-template.conf", "# template")
    _w(root, "/usr/share/tlp/defaults.conf", DEFAULTS_CONF.rstrip("\n"))
    return root


def write_proc_stat(root, step):
    """Fake /proc/stat; each step adds 1000 jiffies of which ~20 % are busy."""
    busy, idle = 1000 + 200 * step, 9000 + 800 * step
    _w(root, "/proc/stat", f"cpu  {busy} 0 0 {idle} 0 0 0 0 0 0")


def read(root, path):
    with open(root + path) as f:
        return f.read().strip()


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else "/tmp/powercontrol-fakeroot"))
