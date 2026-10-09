"""Unit and integration tests that need neither root nor a display.

    python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="powercontrol-test-")
ROOT = os.path.join(TMP, "root")
os.environ["POWERCONTROL_SYSROOT"] = ROOT        # must be set before powerctl is imported
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)

import fakeroot  # noqa: E402
from powerctl import core  # noqa: E402

ENTRY = os.path.join(PROJECT, "powercontrol.py")


def helper(*args):
    r = subprocess.run([sys.executable, ENTRY, *args], capture_output=True, text=True, env=os.environ)
    line = next(ln for ln in r.stdout.splitlines() if ln.startswith(core.RESULT_PREFIX))
    return json.loads(line[len(core.RESULT_PREFIX):])


def rd(path):
    return fakeroot.read(ROOT, path)


class Base(unittest.TestCase):
    def setUp(self):
        fakeroot.build(ROOT)
        cfg = os.environ["XDG_CONFIG_HOME"]
        if os.path.isdir(cfg):
            import shutil
            shutil.rmtree(cfg)


class TestDetection(Base):
    def test_settings_available(self):
        keys = {s.key for s in core.available_settings()}
        self.assertEqual(keys, {"governor", "epp", "boost", "hwp_dyn_boost", "min_perf", "max_perf", "platform",
                                "wifi_pwr", "pcie_aspm", "runtime_pm", "sound_pwr"})

    def test_power_source(self):
        self.assertTrue(core.on_ac())
        fakeroot.build(ROOT, on_ac=False)
        self.assertFalse(core.on_ac())
        self.assertEqual(core.active_column(), "bat")

    def test_status(self):
        st = core.collect_status()
        self.assertEqual(st["battery"]["capacity"], 79)
        self.assertEqual((st["battery"]["start"], st["battery"]["stop"]), (70, 80))
        self.assertAlmostEqual(st["temp"], 51.0)
        self.assertEqual(st["fan"], 1990)
        self.assertEqual(st["runtime"]["pcie_aspm"], "default")
        self.assertEqual(st["loadavg"], (0.85, 0.72, 0.64))

    def test_machine_info_from_fake_root(self):
        self.assertEqual(core.machine_name(), "Example Laptop 14")
        self.assertEqual(core.cpu_model(), "Example Mobile CPU 8-Core")
        self.assertEqual(core.tlp_version(), "1.8.0")
        self.assertEqual(core.service_state("tlp"), "active")
        self.assertEqual(core.service_state("tuned"), "inactive")


class TestTlpConfig(Base):
    def test_tlp_conf_overrides_dropin(self):
        """Regression test for the original bug: /etc/tlp.conf is read after /etc/tlp.d/*.conf."""
        with open(ROOT + core.TLP_DROPIN, "w") as f:
            f.write('STOP_CHARGE_THRESH_BAT0="100"\n')
        self.assertEqual(core.read_tlp_config()["STOP_CHARGE_THRESH_BAT0"][0], "80")
        keys = {c["key"] for c in core.find_conflicts()}
        self.assertIn("STOP_CHARGE_THRESH_BAT0", keys)
        self.assertEqual(len(keys), 13)

    def test_system_state(self):
        st = core.load_system_state()
        self.assertEqual(st["ac"]["governor"], "powersave")
        self.assertEqual(st["bat"]["max_perf"], "30")
        self.assertEqual(st["bat"]["platform"], "low-power")
        self.assertEqual(st["battery"], {"start": 70, "stop": 80})

    def test_render_dropin(self):
        st = core.load_system_state()
        text = core.render_dropin(st)
        self.assertIn('CPU_MAX_PERF_ON_BAT="30"', text)
        self.assertIn('START_CHARGE_THRESH_BAT0="70"', text)


class TestHelper(Base):
    def test_rejects_invalid(self):
        r = helper("--apply", "ac.governor=turbo", "bat.epp=$(id)", "start=90", "stop=80", "evil=1")
        self.assertFalse(r["ok"])
        self.assertEqual(len(r["errors"]), 4)
        self.assertFalse(os.path.exists(ROOT + core.TLP_DROPIN))

    def test_apply_persist_and_remove(self):
        p = core.ProfileStore().get("Battery Saver")
        state = {"ac": p["ac"], "bat": p["bat"], "battery": {"start": 75, "stop": 85}, "profile": "Battery Saver"}
        with open(ROOT + core.TLP_MAIN) as f:
            original = f.read()
        r = helper("--apply", *core.build_apply_args(state, True, True))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["warnings"], [])
        conf = rd(core.TLP_DROPIN)
        self.assertIn('CPU_MAX_PERF_ON_BAT="60"', conf)
        self.assertIn("# Profile: Battery_Saver", conf)
        self.assertEqual(core.find_conflicts(), [])
        self.assertTrue(os.path.exists(ROOT + core.TLP_BACKUP))
        # active column (AC) applied live
        self.assertEqual(rd("/sys/devices/system/cpu/cpu3/cpufreq/energy_performance_preference"),
                         "balance_performance")
        self.assertEqual(rd("/sys/firmware/acpi/platform_profile"), "balanced")
        self.assertEqual(rd("/sys/class/power_supply/BAT0/charge_control_end_threshold"), "85")
        self.assertEqual(core.load_system_state()["bat"]["max_perf"], "60")
        r = helper("--remove-config")
        self.assertTrue(r["ok"], r)
        self.assertFalse(os.path.exists(ROOT + core.TLP_DROPIN))
        with open(ROOT + core.TLP_MAIN) as f:
            self.assertEqual(f.read(), original)

    def test_session_only(self):
        r = helper("--apply", "ac.boost=0", "ac.max_perf=50", "bat.boost=1")
        self.assertTrue(r["ok"], r)
        self.assertEqual(rd("/sys/devices/system/cpu/intel_pstate/no_turbo"), "1")
        self.assertEqual(rd("/sys/devices/system/cpu/intel_pstate/max_perf_pct"), "50")
        self.assertFalse(os.path.exists(ROOT + core.TLP_DROPIN))

    def test_charge_behaviour(self):
        r = helper("--apply", "charge_behaviour=inhibit-charge")
        self.assertTrue(r["ok"], r)
        self.assertEqual(rd("/sys/class/power_supply/BAT0/charge_behaviour"), "inhibit-charge")

    def test_cli_profile(self):
        r = subprocess.run([sys.executable, ENTRY, "--profile", "Maximum"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(rd("/sys/devices/system/cpu/intel_pstate/min_perf_pct"), "100")
        self.assertEqual(rd("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"), "performance")


class TestProfiles(Base):
    def test_builtins(self):
        s = core.ProfileStore()
        self.assertEqual(s.names()[:5], ["Balanced", "Performance", "Maximum", "Battery Saver", "Quiet"])
        m = s.get("Maximum")
        self.assertEqual((m["ac"]["min_perf"], m["bat"]["max_perf"], m["ac"]["boost"]), ("100", "100", "1"))

    def test_builtin_override_revert_reset(self):
        s = core.ProfileStore()
        b = s.get("Balanced")
        s.put("Balanced", b["ac"], dict(b["bat"], max_perf="55"), b["description"])
        self.assertTrue(core.ProfileStore().is_customized("Balanced"))
        s.put("Balanced", b["ac"], dict(b["bat"], max_perf="45"), b["description"])
        self.assertTrue(s.revert("Balanced"))
        self.assertEqual(s.get("Balanced")["bat"]["max_perf"], "55")
        self.assertTrue(s.reset("Balanced"))
        self.assertFalse(s.is_customized("Balanced"))
        self.assertEqual(s.get("Balanced")["bat"], b["bat"])

    def test_custom_history(self):
        s = core.ProfileStore()
        s.put("Mine", {"max_perf": "80"}, {"max_perf": "40"}, "d")
        self.assertFalse(s.can_revert("Mine") or s.can_reset("Mine"))
        s.put("Mine", {"max_perf": "90"}, {"max_perf": "50"}, "d")
        s.put("Mine", {"max_perf": "90"}, {"max_perf": "50"}, "d")  # identical: no new version
        s.put("Mine", {"max_perf": "95"}, {"max_perf": "60"}, "d")
        self.assertEqual(len(s.history("Mine")), 2)
        s.revert("Mine")
        self.assertEqual(s.get("Mine")["bat"]["max_perf"], "50")
        s.reset("Mine")
        self.assertEqual(s.get("Mine")["bat"]["max_perf"], "40")
        s.revert("Mine")  # a reset is itself revertible
        self.assertEqual(s.get("Mine")["bat"]["max_perf"], "50")

    def test_snapshot_reset_all_import_export(self):
        s = core.ProfileStore()
        s.put("A", {"max_perf": "70"}, {}, "")
        path = os.path.join(TMP, "a.json")
        s.export("A", path)
        self.assertEqual(s.import_file(path), "A (2)")
        snap = s.snapshot()
        backup = s.reset_all()
        self.assertTrue(backup and os.path.exists(backup))
        self.assertEqual(core.ProfileStore().custom, {})
        s.restore(snap)
        self.assertEqual(sorted(core.ProfileStore().custom), ["A", "A (2)"])

    def test_invalid_values_dropped(self):
        s = core.ProfileStore()
        s.custom["X"] = {"ac": {"governor": "bogus", "max_perf": "70", "unknown": "1"}, "bat": {}}
        self.assertEqual(s.get("X")["ac"], {"max_perf": "70"})


def tearDownModule():
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
