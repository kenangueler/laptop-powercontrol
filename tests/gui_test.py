"""End-to-end GUI test (and documentation screenshots) against the fake sysfs tree.

Run through tests/run_gui_tests.sh, which starts a private headless mutter compositor:

    tests/run_gui_tests.sh                       # functional test
    tests/run_gui_tests.sh --screenshots DIR     # also write DIR/light/*.png and DIR/dark/*.png
"""
import ctypes
import math
import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="powercontrol-gui-")
os.environ["POWERCONTROL_SYSROOT"] = ROOT = os.path.join(TMP, "root")
os.environ["HOME"] = os.path.join(TMP, "home")  # profiles end up in ~/.config, shown as such in screenshots
os.environ.pop("XDG_CONFIG_HOME", None)
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import fakeroot  # noqa: E402

fakeroot.build(ROOT)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from powerctl import core, gui  # noqa: E402

SHOTS = sys.argv[sys.argv.index("--screenshots") + 1] if "--screenshots" in sys.argv else None
fails = []

# ---------------------------------------------------------------- screenshots (PyGObject cannot wrap GskRenderNode)
_gtk = ctypes.CDLL("libgtk-4.so.1")
ctypes.pythonapi.PyCapsule_GetPointer.restype = ctypes.c_void_p
ctypes.pythonapi.PyCapsule_GetPointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
for _fn, _res, _args in (("gtk_snapshot_to_node", ctypes.c_void_p, [ctypes.c_void_p]),
                         ("gsk_renderer_render_texture", ctypes.c_void_p,
                          [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]),
                         ("gdk_texture_save_to_png", ctypes.c_bool, [ctypes.c_void_p, ctypes.c_char_p])):
    getattr(_gtk, _fn).restype, getattr(_gtk, _fn).argtypes = _res, _args


def _ptr(o):
    return ctypes.pythonapi.PyCapsule_GetPointer(o.__gpointer__, None)


def screenshot(win, name, scheme):
    if not SHOTS:
        return
    os.makedirs(os.path.join(SHOTS, scheme), exist_ok=True)
    with open(os.path.join(SHOTS, "VERSION"), "w") as f:  # lets CI regenerate screenshots once per version
        f.write(core.VERSION + "\n")
    s = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(s, win.get_width(), win.get_height())
    node = _gtk.gtk_snapshot_to_node(_ptr(s))
    tex = _gtk.gsk_renderer_render_texture(_ptr(win.get_renderer()), node, None)
    _gtk.gdk_texture_save_to_png(tex, os.path.join(SHOTS, scheme, f"{name}.png").encode())


# ---------------------------------------------------------------- helpers
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def answer_next_dialog(response):
    orig = Adw.AlertDialog.present

    def present(self, parent):
        Adw.AlertDialog.present = orig
        orig(self, parent)
        GLib.idle_add(lambda: (self.emit("response", response), self.close()) and False)
    Adw.AlertDialog.present = present


def steps(app, seq):
    """Run callables one after another; a callable may return a delay in ms before the next one."""
    def nxt():
        if not seq:
            app.quit()
            return False
        try:
            delay = seq.pop(0)()
        except Exception:
            traceback.print_exc()
            fails.append("exception")
            app.quit()
            return False
        GLib.timeout_add(delay or 50, nxt)
        return False
    GLib.timeout_add(1500, nxt)


def rd(path):
    return fakeroot.read(ROOT, path)


# ---------------------------------------------------------------- test sequence
def scenario(app):
    win = app.props.active_window
    toasts = []
    orig_add = win.toasts.add_toast
    win.toasts.add_toast = lambda t: (toasts.append(t), orig_add(t))
    ed = win.settings.editor

    def edit(col, key, value):
        ed._set(col, key, value)
        ed._changed(col, key)

    def t_startup():
        check(Gtk.IconTheme.get_for_display(win.get_display()).has_icon(core.APP_ICON), "app icon found")
        check(not win.is_dirty(), "clean on startup")
        check(win.column == "ac", "active column is AC")

    def t_profiles_and_editor():
        win.load_profile("Battery Saver")
        check(win.is_dirty() and win.settings.selected_profile() == "Battery Saver", "load profile")
        check(not win.settings.mod_pill.get_visible(), "profile not modified")
        edit("bat", "max_perf", "50")
        check(win.settings.mod_pill.get_visible() and win.a_discard.get_enabled(), "modified marker")
        edit("bat", "min_perf", "70")
        check(ed.get_values()["bat"]["max_perf"] == "70", "min > max clamps max")
        edit("ac", "governor", "performance")
        check(ed.get_values()["ac"]["epp"] == "performance" and not ed.widgets[("ac", "epp")].get_sensitive(),
              "performance governor locks EPP")
        edit("ac", "governor", "powersave")
        check(ed.widgets[("ac", "epp")].get_sensitive(), "EPP unlocked")
        win.discard_changes()
        check(ed.get_values()["bat"]["max_perf"] == "60", "discard unsaved changes")
        win.battery.set_thresholds(60, 85)
        win.battery.start.set_value(90)
        check(int(win.battery.stop.get_value()) == 91, "start >= stop pushes stop")
        win.battery.set_thresholds(60, 85)
        win._copy_columns(None, GLib.Variant("s", "bat"))
        check(ed.get_values()["ac"] == ed.get_values()["bat"], "copy columns")
        win.load_profile("Battery Saver")

    def t_custom_profile():
        v = ed.get_values()
        win.store.put("Work", v["ac"], dict(v["bat"], max_perf="70"), "Office days")
        win.active_profile = "Work"
        win.load_profile("Work")
        edit("bat", "max_perf", "65")
        win.save_profile()
        check(win.store.can_revert("Work"), "save keeps history")
        win.revert_profile("Work")
        check(ed.get_values()["bat"]["max_perf"] == "70", "revert reloads editor")
        prev = win.system.buf.get_text(win.system.buf.get_start_iter(), win.system.buf.get_end_iter(), False)
        check('CPU_MAX_PERF_ON_BAT="70"' in prev and 'STOP_CHARGE_THRESH_BAT0="85"' in prev, "config preview")

    def t_apply():
        win.apply()
        return 100

    def t_wait_apply():
        if win.busy:
            seq.insert(0, t_wait_apply)
            return 100
        check(rd("/sys/class/power_supply/BAT0/charge_control_end_threshold") == "85", "threshold applied")
        check(rd("/sys/firmware/acpi/platform_profile") == "balanced", "AC column applied")
        check('CPU_MAX_PERF_ON_BAT="70"' in rd(core.TLP_DROPIN), "drop-in written")
        check(not core.find_conflicts(), "conflicts disabled")
        check(not win.is_dirty() and not win.banner.get_revealed(), "clean after apply")
        win.reload()
        check(win.active_profile == "Work" and not win.is_dirty(), "profile recognised after reload")

    def t_builtin_reset():
        win.load_profile("Balanced")
        edit("bat", "max_perf", "55")
        win.save_profile()
        check(win.store.is_customized("Balanced"), "built-in customized")
        answer_next_dialog("reset")
        win.reset_profile("Balanced")
        return 300

    def t_builtin_reset_done():
        check(not win.store.is_customized("Balanced") and ed.get_values()["bat"]["max_perf"] == "100",
              "built-in reset to default")
        win.load_profile("Work")

    def t_prepare_screens():
        ov = win.overview
        for i in range(150):  # synthetic history so the charts are not empty in the docs
            ov.ch_power.push(6 + 3 * math.sin(i / 9) + (i % 7) * 0.3)
            ov.ch_temp.push(52 + 9 * math.sin(i / 14))
            ov.ch_freq.push(1.8 + 0.9 * abs(math.sin(i / 11)))
        win.store.put("Gaming", ed.get_values()["ac"], ed.get_values()["ac"], "Docked, everything fast")
        q = win.store.get("Quiet")
        win.store.put("Quiet", q["ac"], dict(q["bat"], max_perf="50"), q["description"])
        win._refresh_profiles()
        edit("bat", "max_perf", "75")
        fakeroot.write_proc_stat(ROOT, 1)  # second sample so the CPU load card shows a value
        win.tick()
        win.toasts.add_toast = lambda t: None  # keep toasts out of the screenshots
        for t in toasts:
            t.dismiss()
        return 600

    def screen_steps():
        out = [t_prepare_screens]
        for dark in (False, True):
            scheme = Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT
            out.append(lambda sc=scheme: Adw.StyleManager.get_default().set_color_scheme(sc))
            for page in ("overview", "settings", "profiles", "battery", "system"):
                out.append(lambda p=page: (win.stack.set_visible_child_name(p), 800)[1])
                out.append(lambda p=page, d=dark: screenshot(win, p, "dark" if d else "light"))
        return out

    def t_reset_all():
        answer_next_dialog("reset")
        win.reset_all_profiles()
        return 300

    def t_reset_all_done():
        check(not win.store.custom and not win.store.overrides, "reset all profiles")
        check(win.active_profile is None, "active profile cleared")

    seq = [t_startup, t_profiles_and_editor, t_custom_profile, t_apply, t_wait_apply, t_builtin_reset,
           t_builtin_reset_done]
    if SHOTS:
        seq += screen_steps()
    seq += [t_reset_all, t_reset_all_done]
    steps(app, seq)


app = gui.App()
app.connect("activate", lambda a: GLib.idle_add(lambda: scenario(a) and False))
app.run([])
import shutil  # noqa: E402

shutil.rmtree(TMP, ignore_errors=True)
print(f"{'OK' if not fails else 'FAILED'}: {len(fails)} failure(s) {fails}")
sys.exit(1 if fails else 0)
