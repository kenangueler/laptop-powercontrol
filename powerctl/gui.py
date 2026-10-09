"""libadwaita user interface."""
import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import core  # noqa: E402
from .core import COLUMN_TITLES, COLUMNS, SMAP  # noqa: E402
from .widgets import (ACCENT, CSS, GREEN, ORANGE, ChartCard, MatrixEditor, StatCard,  # noqa: E402
                      dim, pill)

CURRENT = "Current configuration"
THRESHOLD_PRESETS = [("Maximum lifespan", 40, 50), ("Balanced", 75, 80), ("Full capacity", 95, 100)]


def prop_row(title, value=""):
    r = Adw.ActionRow(title=title, subtitle=value or "-", css_classes=["property"])
    r.set_subtitle_selectable(True)
    return r


def scrolled(child, max_size=1000):
    clamp = Adw.Clamp(maximum_size=max_size, tightening_threshold=max_size - 100, child=child)
    return Gtk.ScrolledWindow(child=clamp, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)


def vbox(spacing=12, margin=0, **kw):
    b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing, **kw)
    for side in ("top", "bottom", "start", "end"):
        getattr(b, f"set_margin_{side}")(margin)
    return b


# =========================================================================== overview page
class OverviewPage(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        content = vbox(18, 24)
        self.append(scrolled(content, 1100))

        # hero
        hero = Gtk.Box(spacing=20, css_classes=["card", "hero"])
        self.bat_icon = Gtk.Image(icon_name="battery-symbolic", pixel_size=72, valign=Gtk.Align.CENTER)
        hero.append(self.bat_icon)
        info = vbox(2, valign=Gtk.Align.CENTER, hexpand=True)
        row = Gtk.Box(spacing=12)
        self.bat_pct = Gtk.Label(label="-", xalign=0, css_classes=["hero-value", "numeric"])
        row.append(self.bat_pct)
        self.src_pill = pill("", "accent")
        row.append(self.src_pill)
        info.append(row)
        self.bat_state = Gtk.Label(xalign=0, css_classes=["title-4"])
        info.append(self.bat_state)
        info.append(dim(f"{core.machine_name()}  ·  {core.cpu_model()}", "caption"))
        hero.append(info)
        side = vbox(4, valign=Gtk.Align.CENTER, halign=Gtk.Align.END)
        side.append(dim("Active profile", "caption-heading", xalign=1))
        self.profile_lbl = Gtk.Label(xalign=1, css_classes=["title-3"])
        side.append(self.profile_lbl)
        self.limit_lbl = dim("", "caption", xalign=1)
        side.append(self.limit_lbl)
        hero.append(side)
        content.append(hero)

        # stat cards
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, min_children_per_line=2,
                           max_children_per_line=3, column_spacing=12, row_spacing=12)
        self.c_power = StatCard("thunderbolt-symbolic", "Power draw")
        self.c_time = StatCard("battery-level-50-symbolic", "Time estimate")
        self.c_freq = StatCard("computer-symbolic", "CPU frequency")
        self.c_load = StatCard("power-profile-performance-symbolic", "CPU load")
        self.c_temp = StatCard("weather-clear-symbolic", "CPU temperature")
        self.c_fan = StatCard("weather-windy-symbolic", "Fan")
        for c in (self.c_power, self.c_time, self.c_freq, self.c_load, self.c_temp, self.c_fan):
            flow.append(c)
            c.get_parent().set_focusable(False)
        content.append(flow)

        charts = Gtk.Box(spacing=12, homogeneous=True)
        self.ch_power = ChartCard("Power draw", ACCENT, "W", floor=5)
        self.ch_temp = ChartCard("CPU temperature", ORANGE, "°C", "{:.0f}", floor=60)
        self.ch_freq = ChartCard("Average CPU frequency", GREEN, "GHz", "{:.2f}", floor=1)
        for c in (self.ch_power, self.ch_temp, self.ch_freq):
            charts.append(c)
        content.append(charts)

        grid = Gtk.Box(spacing=18, homogeneous=True)
        g1 = Adw.PreferencesGroup(title="Live System State", description="Values currently in effect")
        self.live = {}
        for key in ("governor", "epp", "platform", "boost", "hwp_dyn_boost", "perf", "pcie_aspm"):
            if key == "perf":
                if not {"min_perf", "max_perf"} & {s.key for s in core.available_settings()}:
                    continue
                title = "Performance range"
            elif key in SMAP and SMAP[key].requires and not core.exists(SMAP[key].requires):
                continue
            else:
                title = SMAP[key].title
            self.live[key] = prop_row(title)
            g1.add(self.live[key])
        grid.append(g1)
        g2 = Adw.PreferencesGroup(title="Battery", description="Pack status and charge control")
        self.brow = {k: prop_row(t) for k, t in (
            ("thresholds", "Charge thresholds"), ("behaviour", "Charging mode"), ("health", "Health"),
            ("energy", "Energy"), ("cycles", "Charge cycles"), ("voltage", "Voltage"))}
        for r in self.brow.values():
            g2.add(r)
        grid.append(g2)
        content.append(grid)

    def update(self, st):
        b = st["battery"]
        on_ac = st["on_ac"]
        self.src_pill.set_text("PLUGGED IN" if on_ac else "ON BATTERY")
        if b:
            cap = b["capacity"] or 0
            self.bat_pct.set_text(f"{cap} %")
            lvl = min(100, int(round(cap / 10.0)) * 10)
            charging = b["status"] in ("Charging",) or (on_ac and b["status"] != "Discharging")
            icon = "battery-level-100-charged-symbolic" if b["status"] == "Full" else \
                f"battery-level-{lvl}{'-charging' if charging else ''}-symbolic"
            self.bat_icon.set_from_icon_name(icon)
            state = {"Not charging": "Not charging", "Charging": "Charging", "Discharging": "Discharging",
                     "Full": "Fully charged"}.get(b["status"], b["status"])
            if b["time"]:
                d = core.fmt_duration(b["time"][1])
                if d:
                    state += f"  ·  {d} {b['time'][0]}"
            if b["status"] == "Not charging" and b["stop"] and cap >= (b["start"] or 0):
                state += f"  ·  held by charge limit ({b['start']}-{b['stop']} %)"
            self.bat_state.set_text(state)
            p = b["power"]
            self.c_power.set("-" if p is None else f"{p:.1f}", "W",
                             {"Discharging": "from battery", "Charging": "into battery"}.get(b["status"], "idle"))
            if b["time"]:
                self.c_time.set(core.fmt_duration(b["time"][1]) or "-", "", b["time"][0])
            else:
                self.c_time.set("-", "", "on AC power" if on_ac else "estimating…")
            self.ch_power.push(p)
            self.brow["thresholds"].set_subtitle(
                f"Start {b['start']} %  ·  Stop {b['stop']} %" if b["stop"] is not None else "Not supported")
            self.brow["behaviour"].set_subtitle(core.pretty(b["behaviour"]) if b["behaviour"] else "-")
            self.brow["health"].set_subtitle(f"{b['health']:.1f} % of design capacity" if b["health"] else "-")
            if b["energy_now"] and b["energy_full"]:
                self.brow["energy"].set_subtitle(f"{b['energy_now']:.1f} / {b['energy_full']:.1f} Wh"
                                                 + (f"  (design {b['energy_design']:.1f} Wh)"
                                                    if b["energy_design"] else ""))
            self.brow["cycles"].set_subtitle(str(b["cycles"]) if b["cycles"] is not None else "-")
            self.brow["voltage"].set_subtitle(f"{b['voltage']:.2f} V" if b["voltage"] else "-")
        else:
            self.bat_pct.set_text("No battery")
            self.bat_state.set_text("Running on AC power")
        if st["freq_avg"]:
            self.c_freq.set(f"{st['freq_avg'] / 1000:.2f}", "GHz",
                            f"peak {st['freq_max'] / 1000:.2f} GHz · {st['cpus']} threads")
            self.ch_freq.push(st["freq_avg"] / 1000)
        if st["load"] is not None:
            la = st["loadavg"]
            self.c_load.set(f"{st['load']:.0f}", "%",
                            f"load average {la[0]:.2f} {la[1]:.2f} {la[2]:.2f}" if la and len(la) == 3 else "")
        t = st["temp"]
        self.c_temp.set("-" if t is None else f"{t:.0f}", "°C", "package sensor")
        self.ch_temp.push(t)
        f = st["fan"]
        self.c_fan.set("-" if f is None else ("Off" if f == 0 else f"{f}"), "" if not f else "RPM",
                       "fan speed" if f is not None else "no fan sensor")
        rt = st["runtime"]
        for k, row in self.live.items():
            if k == "perf":
                row.set_subtitle(f"{rt.get('min_perf', '?')} to {rt.get('max_perf', '?')} %")
            elif k in rt:
                row.set_subtitle(SMAP[k].label(rt[k]))
        self.profile_lbl.set_text(self.win.profile_display())
        rtp = rt.get("platform")
        self.limit_lbl.set_text(f"Platform: {core.pretty(rtp)}" if rtp else "")


# =========================================================================== power settings page
class SettingsPage(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.editor = MatrixEditor()

        top = vbox(12)
        top.set_margin_top(18)
        top.set_margin_start(24)
        top.set_margin_end(24)
        bar = Gtk.Box(spacing=10, css_classes=["card", "profile-bar"])
        bar.append(Gtk.Image(icon_name="starred-symbolic"))
        bar.append(Gtk.Label(label="Profile", css_classes=["heading"]))
        self.profile_dd = Gtk.DropDown(model=Gtk.StringList(), width_request=240)
        self.profile_dd.connect("notify::selected", self._on_profile_selected)
        bar.append(self.profile_dd)
        self.mod_pill = pill("MODIFIED", "warning")
        bar.append(self.mod_pill)
        self.desc = dim("", "caption", hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        bar.append(self.desc)
        save = Gtk.Button(icon_name="document-save-symbolic", tooltip_text="Save profile (Ctrl+S)",
                          action_name="win.save-profile")
        save_as = Gtk.Button(label="Save As…", action_name="win.save-profile-as")
        menu = Gio.Menu()
        sec = Gio.Menu()
        sec.append("Copy Plugged in → On battery", "win.copy-columns::ac")
        sec.append("Copy On battery → Plugged in", "win.copy-columns::bat")
        menu.append_section(None, sec)
        sec2 = Gio.Menu()
        sec2.append("Discard Unsaved Changes", "win.discard-changes")
        sec2.append("Revert to System Configuration", "win.reload")
        menu.append_section(None, sec2)
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, tooltip_text="More")
        for w in (save, save_as, more):
            bar.append(w)
        top.append(bar)
        top.append(self.editor.header())
        self.append(Adw.Clamp(maximum_size=1000, tightening_threshold=900, child=top))

        content = vbox(6)
        content.set_margin_start(24)
        content.set_margin_end(24)
        content.set_margin_bottom(24)
        content.append(self.editor)
        note = dim("The highlighted column is in effect right now. TLP switches to the other column "
                   "automatically when the power source changes.", "caption", wrap=True, margin_top=12)
        content.append(note)
        self.append(scrolled(content))
        self.editor.connect("changed", lambda *_: win.on_state_changed())
        self._suppress = False

    def set_profiles(self, names, selected):
        self._suppress = True
        items = [CURRENT] + names
        self.profile_dd.set_model(Gtk.StringList.new(items))
        self.profile_dd.set_selected(items.index(selected) if selected in items else 0)
        self._suppress = False

    def selected_profile(self):
        i = self.profile_dd.get_selected()
        m = self.profile_dd.get_model()
        name = m.get_string(i) if m and i < m.get_n_items() else None
        return None if name in (None, CURRENT) else name

    def _on_profile_selected(self, *_):
        if not self._suppress:
            self.win.load_profile(self.selected_profile())


# =========================================================================== profiles page
class ProfilesPage(Adw.PreferencesPage):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.groups = []

    def rebuild(self):
        for g in self.groups:
            self.remove(g)
        self.groups = []
        store = self.win.store
        g1 = Adw.PreferencesGroup(title="Built-in Profiles",
                                  description="Curated presets. You can edit and save them, and reset them to "
                                              "their defaults at any time.")
        for n in store.builtin:
            g1.add(self._row(n, True))
        g2 = Adw.PreferencesGroup(title="Your Profiles", description=f"Stored in {core.display_path(core.CONFIG_DIR)}")
        hb = Gtk.Box(spacing=6)
        hb.append(Gtk.Button(label="Import…", action_name="win.import-profile", css_classes=["flat"]))
        nb = Gtk.Button(action_name="win.save-profile-as", css_classes=["flat"])
        nb.set_child(Adw.ButtonContent(icon_name="list-add-symbolic", label="New from Current"))
        hb.append(nb)
        g2.set_header_suffix(hb)
        if store.custom:
            for n in sorted(store.custom, key=str.lower):
                g2.add(self._row(n, False))
        else:
            r = Adw.ActionRow(title="No custom profiles yet",
                              subtitle="Adjust the settings on the Power Settings page and choose “Save As…”")
            r.add_css_class("dim-label")
            g2.add(r)
        g3 = Adw.PreferencesGroup(title="Reset")
        n_custom, n_over = len(store.custom), len(store.overrides)
        ra = Adw.ActionRow(title="Reset all profiles",
                           subtitle=f"Deletes {n_custom} custom profile{'s' if n_custom != 1 else ''} and resets "
                                    f"{n_over} modified built-in profile{'s' if n_over != 1 else ''}. "
                                    "A backup file is kept.")
        rb = Gtk.Button(label="Reset All…", valign=Gtk.Align.CENTER, css_classes=["destructive-action"],
                        action_name="win.profiles-reset-all")
        rb.set_sensitive(bool(n_custom or n_over))
        ra.add_suffix(rb)
        g3.add(ra)
        for g in (g1, g2, g3):
            self.add(g)
            self.groups.append(g)

    def _summary(self, p):
        parts = []
        for c in COLUMNS:
            v = p[c]
            bits = [core.pretty(v[k]) for k in ("platform", "epp") if k in v]
            if "boost" in v:
                bits.append("turbo" if v["boost"] == "1" else "no turbo")
            if v.get("max_perf") and v["max_perf"] != "100":
                bits.append(f"max {v['max_perf']} %")
            parts.append(f"{COLUMN_TITLES[c]}: {', '.join(bits) or 'default'}")
        return "\n".join(parts)

    def _row(self, name, builtin):
        store = self.win.store
        p = store.get(name)
        sub = (p["description"] + "\n" if p["description"] else "") + self._summary(p)
        if p.get("modified") and (not builtin or store.is_customized(name)):
            n = len(store.history(name))
            sub += f"\nSaved {p['modified']}" + (f"  ·  {n} earlier version{'s' if n != 1 else ''}" if n else "")
        row = Adw.ActionRow(title=GLib.markup_escape_text(name), subtitle=GLib.markup_escape_text(sub))
        row.set_subtitle_lines(0)
        row.add_prefix(Gtk.Image(icon_name=p["icon"], pixel_size=24))
        if builtin and store.is_customized(name):
            row.add_suffix(pill("CUSTOMIZED", "warning"))
        if name == self.win.active_profile:
            row.add_suffix(pill("ACTIVE", "success"))
        v = GLib.Variant("s", name)
        load = Gtk.Button(label="Edit", valign=Gtk.Align.CENTER, css_classes=["flat"],
                          tooltip_text="Load into the Power Settings editor")
        load.set_action_name("win.profile-load")
        load.set_action_target_value(v)
        apply = Gtk.Button(label="Apply", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
        apply.set_action_name("win.profile-apply")
        apply.set_action_target_value(v)
        menu = Gio.Menu()
        sections = []
        rev = []
        if store.can_revert(name):
            when = store.history(name)[0].get("modified")
            rev.append((f"Revert to Previous Version ({when})" if when else "Revert to Previous Version",
                        "win.profile-revert"))
        if store.can_reset(name):
            rev.append(("Reset to Default…" if builtin else "Reset to Original…", "win.profile-reset"))
        if rev:
            sections.append(rev)
        items = [("Duplicate…", "win.profile-duplicate")]
        if not builtin:
            items.append(("Rename…", "win.profile-rename"))
        items.append(("Export…", "win.profile-export"))
        sections.append(items)
        if not builtin:
            sections.append([("Delete…", "win.profile-delete")])
        for sec_items in sections:
            sec = Gio.Menu()
            for label, action in sec_items:
                it = Gio.MenuItem.new(label, None)
                it.set_action_and_target_value(action, v)
                sec.append_item(it)
            menu.append_section(None, sec)
        mb = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, valign=Gtk.Align.CENTER,
                            css_classes=["flat"])
        for w in (load, apply, mb):
            row.add_suffix(w)
        return row


# =========================================================================== battery page
class BatteryPage(Adw.PreferencesPage):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.bat = core.battery_name()
        sp, ep = core.charge_thresholds_paths(self.bat) if self.bat else (None, None)
        self.supported = bool(ep)
        self._loading = False

        g = Adw.PreferencesGroup(
            title="Charge Thresholds",
            description="Keeping a lithium battery below 100 % slows down wear considerably. Charging starts "
                        "when the level drops below the start threshold and stops at the stop threshold.")
        presets = Gtk.Box(spacing=0, css_classes=["linked"], valign=Gtk.Align.CENTER)
        for label, s, e in THRESHOLD_PRESETS:
            b = Gtk.Button(label=label, tooltip_text=f"Start {s} %, stop {e} %")
            b.connect("clicked", lambda _b, s=s, e=e: self.set_thresholds(s, e))
            presets.append(b)
        pr = Adw.ActionRow(title="Presets")
        pr.add_suffix(presets)
        g.add(pr)
        self.start = Adw.SpinRow.new_with_range(0, 99, 1)
        self.start.set_title("Start charging below")
        self.start.set_subtitle("Percent")
        self.stop = Adw.SpinRow.new_with_range(1, 100, 1)
        self.stop.set_title("Stop charging at")
        self.stop.set_subtitle("Percent")
        for r in (self.start, self.stop):
            r.connect("notify::value", self._changed)
            g.add(r)
        self.bar = Gtk.LevelBar(min_value=0, max_value=100, hexpand=True, valign=Gtk.Align.CENTER,
                                css_classes=["threshold-bar"], width_request=260)
        self.bar.remove_offset_value("low")
        self.bar.remove_offset_value("high")
        self.bar.remove_offset_value("full")
        vis = Adw.ActionRow(title="Current level")
        self.bar_lbl = dim("", "numeric")
        vis.add_suffix(self.bar_lbl)
        vis.add_suffix(self.bar)
        g.add(vis)
        g.set_sensitive(self.supported)
        self.add(g)

        cur, opts = core.charge_behaviours()
        g2 = Adw.PreferencesGroup(title="Charging Mode",
                                  description="Overrides charging immediately. Resets to Normal after a reboot.")
        self.behaviours = opts
        self.mode = Adw.ComboRow(title="Mode", model=Gtk.StringList.new([core.pretty(o) for o in opts]))
        btn = Gtk.Button(label="Set Now", valign=Gtk.Align.CENTER)
        btn.connect("clicked", self._set_mode)
        self.mode.add_suffix(btn)
        if cur in opts:
            self.mode.set_selected(opts.index(cur))
        g2.add(self.mode)
        g2.set_sensitive(bool(opts))
        self.add(g2)

        g3 = Adw.PreferencesGroup(title="Battery Information")
        self.info = {k: prop_row(t) for k, t in (
            ("model", "Model"), ("tech", "Technology"), ("design", "Design capacity"), ("full", "Full charge capacity"),
            ("health", "Health"), ("cycles", "Cycle count"))}
        for r in self.info.values():
            g3.add(r)
        self.add(g3)

    def set_thresholds(self, s, e, emit=True):
        self._loading = True
        self.stop.set_value(e)
        self.start.set_value(s)
        self._loading = False
        if emit:
            self.win.on_state_changed()

    def get(self):
        if not self.supported:
            return {}
        return {"start": int(self.start.get_value()), "stop": int(self.stop.get_value())}

    def _changed(self, row, _p):
        if self._loading:
            return
        self._loading = True
        s, e = int(self.start.get_value()), int(self.stop.get_value())
        if s >= e:
            if row is self.start:
                self.stop.set_value(min(100, s + 1))
            else:
                self.start.set_value(max(0, e - 1))
        self._loading = False
        self.win.on_state_changed()

    def _set_mode(self, _b):
        i = self.mode.get_selected()
        if 0 <= i < len(self.behaviours):
            self.win.run_privileged([f"charge_behaviour={self.behaviours[i]}"], "Charging mode updated")

    def update(self, st):
        b = st["battery"]
        if not b:
            return
        cap = b["capacity"] or 0
        self.bar.set_value(cap)
        self.bar_lbl.set_text(f"{cap} %")
        self.info["model"].set_subtitle(" ".join(x for x in (b["manufacturer"], b["model"]) if x) or "-")
        self.info["tech"].set_subtitle(b["technology"] or "-")
        self.info["design"].set_subtitle(f"{b['energy_design']:.1f} Wh" if b["energy_design"] else "-")
        self.info["full"].set_subtitle(f"{b['energy_full']:.1f} Wh" if b["energy_full"] else "-")
        self.info["health"].set_subtitle(f"{b['health']:.1f} %" if b["health"] else "-")
        self.info["cycles"].set_subtitle(str(b["cycles"]) if b["cycles"] is not None else "-")


# =========================================================================== system page
class SystemPage(Adw.PreferencesPage):
    def __init__(self, win):
        super().__init__()
        self.win = win
        g = Adw.PreferencesGroup(title="Power Management Backend")
        tlp = core.tlp_version()
        self.r_tlp = prop_row("TLP", f"Version {tlp}" if tlp else "Not installed")
        self.r_svc = prop_row("TLP service")
        self.r_drv = prop_row("CPU scaling driver",
                              f"{core.scaling_driver()}" + (f" ({core.rd(core.PSTATE + '/status')} mode)"
                                                            if core.exists(core.PSTATE + "/status") else ""))
        self.r_hw = prop_row("Hardware", f"{core.machine_name()}  ·  {core.cpu_model()}")
        self.r_other = prop_row("Competing services")
        for r in (self.r_tlp, self.r_svc, self.r_drv, self.r_hw, self.r_other):
            g.add(r)
        self.add(g)

        g2 = Adw.PreferencesGroup(title="Behaviour")
        self.persist = Adw.SwitchRow(title="Save to TLP configuration",
                                     subtitle="Settings survive reboots and follow the power source automatically. "
                                              "When off, only the active column is applied until the next change.")
        self.persist.set_active(win.prefs["persist"] and bool(core.tlp_binary()))
        self.persist.set_sensitive(bool(core.tlp_binary()))
        self.fix = Adw.SwitchRow(title="Disable overriding entries",
                                 subtitle="TLP reads /etc/tlp.conf after drop-in files, so matching entries there "
                                          "would silently override this app. They are commented out (with a backup).")
        self.fix.set_active(win.prefs["fix_conflicts"])
        self.persist.bind_property("active", self.fix, "sensitive", 2)  # SYNC_CREATE
        for r in (self.persist, self.fix):
            r.connect("notify::active", self._pref_changed)
            g2.add(r)
        self.add(g2)

        self.g_conf = Adw.PreferencesGroup(title="Overriding Entries")
        self.add(self.g_conf)
        self.conf_rows = []

        g4 = Adw.PreferencesGroup(title="Configuration Preview",
                                  description=f"Contents of {core.TLP_DROPIN} after applying")
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy", css_classes=["flat"])
        copy.connect("clicked", lambda *_: Gdk.Display.get_default().get_clipboard().set(self.buf.get_text(
            self.buf.get_start_iter(), self.buf.get_end_iter(), False)))
        g4.set_header_suffix(copy)
        self.buf = Gtk.TextBuffer()
        tv = Gtk.TextView(buffer=self.buf, editable=False, monospace=True, cursor_visible=False,
                          css_classes=["preview"], wrap_mode=Gtk.WrapMode.NONE)
        frame = Gtk.ScrolledWindow(child=tv, min_content_height=200, max_content_height=360,
                                   propagate_natural_height=True, css_classes=["card"])
        g4.add(frame)
        self.add(g4)

        g5 = Adw.PreferencesGroup(title="Maintenance")
        rm = Adw.ActionRow(title="Remove generated configuration",
                           subtitle="Deletes the drop-in file and re-enables entries this app disabled")
        b = Gtk.Button(label="Remove…", valign=Gtk.Align.CENTER, css_classes=["destructive-action"],
                       action_name="win.remove-config")
        rm.add_suffix(b)
        g5.add(rm)
        of = Adw.ActionRow(title="Profiles folder", subtitle=core.display_path(core.CONFIG_DIR), activatable=True)
        of.add_suffix(Gtk.Image(icon_name="folder-open-symbolic"))
        of.connect("activated", lambda *_: win.open_folder())
        g5.add(of)
        self.add(g5)
        self.refresh_backend()

    def _pref_changed(self, *_):
        self.win.prefs["persist"] = self.persist.get_active()
        self.win.prefs["fix_conflicts"] = self.fix.get_active()
        core.save_prefs(self.win.prefs)

    def refresh_backend(self):
        svc = core.service_state("tlp")
        self.r_svc.set_subtitle({"active": "Active", "inactive": "Inactive (enable with: systemctl enable --now tlp)"}
                                .get(svc, svc.capitalize()))
        others = [n for n in ("power-profiles-daemon", "tuned", "auto-cpufreq", "thermald")
                  if n != "thermald" and core.service_state(n) == "active"]
        self.r_other.set_subtitle(("Running: " + ", ".join(others) + " (may conflict with TLP)") if others
                                  else "None detected")
        for r in self.conf_rows:
            self.g_conf.remove(r)
        self.conf_rows = []
        conflicts = core.find_conflicts()
        if conflicts:
            self.g_conf.set_description(
                f"{len(conflicts)} entr{'y' if len(conflicts) == 1 else 'ies'} read after this app's configuration "
                "currently take precedence. " + ("They will be disabled on the next Apply."
                                                 if self.fix.get_active() else "Enable the option above to fix this."))
            for c in conflicts:
                r = Adw.ActionRow(title=c["key"], subtitle=f"{c['file']}, line {c['line']}  ·  value “{c['value']}”")
                r.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
                self.g_conf.add(r)
                self.conf_rows.append(r)
        else:
            self.g_conf.set_description("Nothing overrides this app's settings.")
            r = Adw.ActionRow(title="No conflicts")
            r.add_prefix(Gtk.Image(icon_name="object-select-symbolic", css_classes=["success"]))
            self.g_conf.add(r)
            self.conf_rows.append(r)

    def set_preview(self, text):
        self.buf.set_text(text)


# =========================================================================== main window
class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=core.APP_NAME, default_width=1080, default_height=820)
        self.set_size_request(760, 520)
        self.store = core.ProfileStore()
        self.prefs = core.load_prefs()
        self.active_profile = self.prefs.get("active_profile")
        self.system_state = core.load_system_state()
        self.busy = False
        self.column = core.active_column()

        self.toasts = Adw.ToastOverlay()
        tv = Adw.ToolbarView()
        self.toasts.set_child(tv)
        self.set_content(self.toasts)

        header = Adw.HeaderBar()
        self.stack = Adw.ViewStack()
        sw = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(sw)
        reload_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Reload from system (F5)",
                                action_name="win.reload")
        header.pack_start(reload_btn)
        menu = Gio.Menu()
        menu.append("Open Profiles Folder", "win.open-folder")
        menu.append("Keyboard Shortcuts", "win.shortcuts")
        menu.append(f"About {core.APP_NAME}", "win.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True,
                                       tooltip_text="Main menu"))
        self.apply_btn = Gtk.Button(css_classes=["suggested-action"], action_name="win.apply",
                                    tooltip_text="Apply settings (Ctrl+Enter)")
        ab = Gtk.Box(spacing=6)
        self.spinner = Gtk.Spinner(visible=False)
        ab.append(self.spinner)
        ab.append(Gtk.Label(label="Apply"))
        self.apply_btn.set_child(ab)
        header.pack_end(self.apply_btn)
        tv.add_top_bar(header)

        self.banner = Adw.Banner(title="You have unapplied changes", button_label="Apply")
        self.banner.set_action_name("win.apply")
        tv.add_top_bar(self.banner)

        self.overview = OverviewPage(self)
        self.settings = SettingsPage(self)
        self.profiles = ProfilesPage(self)
        self.battery = BatteryPage(self)
        self.system = SystemPage(self)
        for page, name, title, icon in (
                (self.overview, "overview", "Overview", "gnome-power-manager-symbolic"),
                (self.settings, "settings", "Power Settings", "power-profile-balanced-symbolic"),
                (self.profiles, "profiles", "Profiles", "starred-symbolic"),
                (self.battery, "battery", "Battery", "battery-symbolic"),
                (self.system, "system", "System", "emblem-system-symbolic")):
            self.stack.add_titled_with_icon(page, name, title, icon)
        tv.set_content(self.stack)
        bar = Adw.ViewSwitcherBar(stack=self.stack)
        tv.add_bottom_bar(bar)
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 900sp"))
        bp.add_setter(sw, "visible", False)
        bp.add_setter(bar, "reveal", True)
        self.add_breakpoint(bp)

        self._actions()
        self._load_into_ui(self.system_state)
        if self.active_profile and not self.store.get(self.active_profile):
            self.active_profile = None
        if self.active_profile and not self._matches(self.store.get(self.active_profile)):
            match = self._find_matching_profile()
            self.active_profile = match
        elif not self.active_profile:
            self.active_profile = self._find_matching_profile()
        self._refresh_profiles()
        self.settings.editor.set_active_column(self.column)
        self.on_state_changed()
        self.tick()
        GLib.timeout_add_seconds(2, self.tick)

    # ---- actions
    def _actions(self):
        def add(name, cb, param=None):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
            a.connect("activate", cb)
            self.add_action(a)
            return a

        self.a_apply = add("apply", lambda *_: self.apply())
        add("reload", lambda *_: self.reload())
        add("save-profile", lambda *_: self.save_profile())
        add("save-profile-as", lambda *_: self.save_profile_as())
        add("import-profile", lambda *_: self.import_profile())
        add("copy-columns", self._copy_columns, "s")
        add("profile-load", lambda _a, v: self.load_profile(v.get_string(), switch=True), "s")
        add("profile-apply", lambda _a, v: self.apply_profile(v.get_string()), "s")
        add("profile-duplicate", lambda _a, v: self.save_profile_as(source=v.get_string()), "s")
        add("profile-rename", lambda _a, v: self.rename_profile(v.get_string()), "s")
        add("profile-delete", lambda _a, v: self.delete_profile(v.get_string()), "s")
        add("profile-revert", lambda _a, v: self.revert_profile(v.get_string()), "s")
        add("profile-reset", lambda _a, v: self.reset_profile(v.get_string()), "s")
        add("profiles-reset-all", lambda *_: self.reset_all_profiles())
        self.a_discard = add("discard-changes", lambda *_: self.discard_changes())
        add("profile-export", lambda _a, v: self.export_profile(v.get_string()), "s")
        add("remove-config", lambda *_: self.remove_config())
        add("open-folder", lambda *_: self.open_folder())
        add("about", lambda *_: self.about())
        add("shortcuts", lambda *_: self.shortcuts())

    # ---- state
    def current_state(self):
        v = self.settings.editor.get_values()
        v["battery"] = self.battery.get()
        return v

    def _load_into_ui(self, state):
        self.settings.editor.set_values(state, emit=False)
        b = state.get("battery") or {}
        if "start" in b and "stop" in b:
            self.battery.set_thresholds(b["start"], b["stop"], emit=False)
        # normalised copy (e.g. EPP forced by the performance governor) used for change tracking
        self.baseline = self.current_state()

    def _matches(self, profile):
        if not profile:
            return False
        cur = self.settings.editor.get_values()
        return all(cur[c].get(k) == v for c in COLUMNS for k, v in profile[c].items())

    def _find_matching_profile(self):
        return next((n for n in self.store.names() if self._matches(self.store.get(n))), None)

    def is_dirty(self):
        cur, sys_ = self.current_state(), self.baseline
        return any(cur[c] != sys_.get(c, {}) for c in COLUMNS) or \
            (cur["battery"] and cur["battery"] != sys_.get("battery"))

    def profile_display(self):
        name = self.active_profile or "Custom"
        p = self.store.get(self.active_profile) if self.active_profile else None
        return name + (" (modified)" if p and not self._matches(p) else "")

    def on_state_changed(self):
        dirty = self.is_dirty()
        self.banner.set_revealed(dirty)
        p = self.store.get(self.active_profile) if self.active_profile else None
        modified = bool(p) and not self._matches(p)
        self.settings.mod_pill.set_visible(modified)
        self.a_discard.set_enabled(modified)
        self.settings.desc.set_text(p["description"] if p else "Settings loaded from the system")
        st = self.current_state()
        st["profile"] = self.active_profile
        self.system.set_preview(core.render_dropin(st))

    def _refresh_profiles(self):
        self.settings.set_profiles(self.store.names(), self.active_profile or CURRENT)
        self.profiles.rebuild()

    def load_profile(self, name, switch=False):
        p = self.store.get(name) if name else None
        if name and not p:
            return
        self.active_profile = name
        self.settings.editor.set_values(p if p else self.system_state, emit=False)
        self._refresh_profiles()
        self.on_state_changed()
        if switch:
            self.stack.set_visible_child_name("settings")
            self.toast(f"Loaded “{name}”. Review and press Apply")

    def apply_profile(self, name):
        self.load_profile(name)
        self.apply()

    def _copy_columns(self, _a, v):
        src = v.get_string()
        dst = "bat" if src == "ac" else "ac"
        vals = self.settings.editor.get_values()
        self.settings.editor.set_values({dst: vals[src]})
        self.toast(f"Copied {COLUMN_TITLES[src]} → {COLUMN_TITLES[dst]}")

    def reload(self):
        self.system_state = core.load_system_state()
        self._load_into_ui(self.system_state)
        self.active_profile = self.active_profile if self._matches(self.store.get(self.active_profile)) \
            else self._find_matching_profile()
        self._refresh_profiles()
        self.system.refresh_backend()
        self.on_state_changed()
        self.toast("Reloaded from system")

    # ---- periodic
    def tick(self):
        st = core.collect_status()
        col = "ac" if st["on_ac"] else "bat"
        if col != self.column:
            self.column = col
            self.settings.editor.set_active_column(col)
            self.toast(f"Power source changed: {COLUMN_TITLES[col].lower()}")
        self.overview.update(st)
        self.battery.update(st)
        return True

    # ---- privileged operations
    def set_busy(self, busy):
        self.busy = busy
        self.spinner.set_visible(busy)
        self.spinner.set_spinning(busy)
        self.a_apply.set_enabled(not busy)

    def apply(self):
        if self.busy:
            return
        st = self.current_state()
        st["profile"] = self.active_profile
        persist = self.system.persist.get_active()
        self.run_privileged(core.build_apply_args(st, persist, persist and self.system.fix.get_active()),
                            "Settings applied" + ("" if persist else " for this session"), reload=True)

    def run_privileged(self, args, success, reload=False, action="--apply"):
        self.set_busy(True)

        def work():
            res = core.run_helper(args, action)
            GLib.idle_add(self._done, res, success, reload)

        threading.Thread(target=work, daemon=True).start()

    def _done(self, res, success, reload):
        self.set_busy(False)
        if res.get("cancelled"):
            self.toast("Authentication cancelled")
            return False
        if reload == "full":
            self.reload()
        elif reload:
            self.system_state = core.load_system_state()
            self.baseline = self.current_state() if res["ok"] else self.baseline
            self.prefs["active_profile"] = self.active_profile
            core.save_prefs(self.prefs)
            self.on_state_changed()
            self.system.refresh_backend()
            self._refresh_profiles()
        self.tick()
        if res["errors"]:
            self.alert("Could not apply all settings", res["errors"] + res["warnings"], res["notes"])
        elif res["warnings"]:
            t = Adw.Toast(title=f"{success} with {len(res['warnings'])} warning(s)", button_label="Details",
                          timeout=8)
            t.connect("button-clicked", lambda *_: self.alert("Applied with warnings", res["warnings"],
                                                              res["notes"]))
            self.toasts.add_toast(t)
        else:
            self.toast(success)
        return False

    def remove_config(self):
        d = Adw.AlertDialog(heading="Remove generated configuration?",
                            body=f"{core.TLP_DROPIN} will be deleted and entries in /etc/tlp.conf that were "
                                 "disabled by this app will be restored. TLP will then use its own configuration.")
        d.add_response("cancel", "Cancel")
        d.add_response("remove", "Remove")
        d.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_close_response("cancel")
        d.connect("response", lambda _d, r: r == "remove" and self.run_privileged(
            [], "Configuration removed", reload="full", action="--remove-config"))
        d.present(self)

    # ---- profile management
    def _name_dialog(self, heading, body, initial, cb, desc=None, confirm="Save"):
        d = Adw.AlertDialog(heading=heading, body=body)
        box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        name = Adw.EntryRow(title="Name", text=initial)
        box.append(name)
        descr = None
        if desc is not None:
            descr = Adw.EntryRow(title="Description (optional)", text=desc)
            box.append(descr)
        d.set_extra_child(box)
        d.add_response("cancel", "Cancel")
        d.add_response("ok", confirm)
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("ok")
        d.set_close_response("cancel")

        def validate(*_):
            n = name.get_text().strip()
            ok = bool(n) and len(n) <= 60 and n != CURRENT and not self.store.is_builtin(n) and \
                (n == initial or n not in self.store.custom or heading.startswith("Save"))
            d.set_response_enabled("ok", ok)
        name.connect("changed", validate)
        name.connect("entry-activated", lambda *_: d.get_response_enabled("ok") and (
            d.close(), cb(name.get_text().strip(), descr.get_text().strip() if descr else "")))
        validate()
        d.connect("response", lambda _d, r: r == "ok" and cb(name.get_text().strip(),
                                                              descr.get_text().strip() if descr else ""))
        d.present(self)
        name.grab_focus()

    def save_profile(self):
        if not self.active_profile:
            self.save_profile_as()
            return
        name = self.active_profile
        p = self.store.get(name)
        v = self.settings.editor.get_values()
        snap = self.store.snapshot()
        self.store.put(name, v["ac"], v["bat"], p["description"])
        self._refresh_profiles()
        self.on_state_changed()
        self.undo_toast(f"Saved “{name}”" + (" (reset to default any time from Profiles)"
                                               if self.store.is_customized(name) else ""), snap)

    def save_profile_as(self, source=None):
        if source:
            p = self.store.get(source)
            initial, desc = f"{source} (copy)", p["description"]
        else:
            p = None
            initial = "" if not self.active_profile or self.store.is_builtin(self.active_profile) \
                else self.active_profile
            desc = self.store.get(self.active_profile)["description"] if self.active_profile else ""

        def done(name, description):
            vals = p if p else self.settings.editor.get_values()
            self.store.put(name, vals["ac"], vals["bat"], description)
            if not p:
                self.active_profile = name
            self._refresh_profiles()
            self.on_state_changed()
            self.toast(f"Profile “{name}” saved")

        self._name_dialog("Duplicate Profile" if source else "Save Profile",
                          "Saves both the plugged-in and on-battery columns.", initial, done, desc)

    def undo_toast(self, text, snap):
        t = Adw.Toast(title=GLib.markup_escape_text(text), button_label="Undo", timeout=6)

        def undo(*_):
            self.store.restore(snap)
            self._after_profile_change(None)
            self.toast("Undone")
        t.connect("button-clicked", undo)
        self.toasts.add_toast(t)

    def _after_profile_change(self, name):
        """Refresh after a profile was modified outside the editor; reload it if it is being edited."""
        if self.active_profile and not self.store.get(self.active_profile):
            self.active_profile = None
        if name and name == self.active_profile:
            self.settings.editor.set_values(self.store.get(name), emit=False)
        self._refresh_profiles()
        self.on_state_changed()

    def discard_changes(self):
        p = self.store.get(self.active_profile) if self.active_profile else None
        if p:
            self.settings.editor.set_values(p, emit=False)
            self.on_state_changed()
            self.toast(f"Discarded changes to “{self.active_profile}”")

    def revert_profile(self, name):
        snap = self.store.snapshot()
        when = (self.store.history(name) or [{}])[0].get("modified", "")
        if self.store.revert(name):
            self._after_profile_change(name)
            self.undo_toast(f"“{name}” reverted to version of {when}" + self._apply_hint(name), snap)

    def _apply_hint(self, name):
        return ". Press Apply to use it" if name == self.active_profile and self.is_dirty() else ""

    def reset_profile(self, name):
        builtin = self.store.is_builtin(name)
        d = Adw.AlertDialog(
            heading=f"Reset “{name}”?",
            body=("Your changes are discarded and the built-in default values are restored."
                  if builtin else "The profile is restored to the settings it was created with. "
                                  "The current version is kept in its history."))
        d.add_response("cancel", "Cancel")
        d.add_response("reset", "Reset")
        d.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_close_response("cancel")

        def resp(_d, r):
            if r != "reset":
                return
            snap = self.store.snapshot()
            if self.store.reset(name):
                self._after_profile_change(name)
                self.undo_toast(f"“{name}” reset to {'default' if builtin else 'original'}"
                                + self._apply_hint(name), snap)
        d.connect("response", resp)
        d.present(self)

    def reset_all_profiles(self):
        d = Adw.AlertDialog(heading="Reset all profiles?",
                            body="All custom profiles are deleted and every built-in profile returns to its "
                                 "default values. A backup of the profiles file is kept in the profiles folder.")
        d.add_response("cancel", "Cancel")
        d.add_response("reset", "Reset All")
        d.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_close_response("cancel")

        def resp(_d, r):
            if r != "reset":
                return
            snap = self.store.snapshot()
            active = self.active_profile
            try:
                backup = self.store.reset_all()
            except OSError as e:
                self.alert("Reset failed", [str(e)])
                return
            self._after_profile_change(active)
            self.undo_toast("All profiles reset" + (f", backup: {os.path.basename(backup)}" if backup else ""),
                            snap)
        d.connect("response", resp)
        d.present(self)

    def rename_profile(self, old):
        def done(new, _d):
            if new == old:
                return
            self.store.rename(old, new)
            if self.active_profile == old:
                self.active_profile = new
            self._refresh_profiles()
            self.toast(f"Renamed to “{new}”")
        self._name_dialog("Rename Profile", "", old, done, confirm="Rename")

    def delete_profile(self, name):
        d = Adw.AlertDialog(heading=f"Delete “{name}”?",
                            body="The profile and its version history are removed.")
        d.add_response("cancel", "Cancel")
        d.add_response("delete", "Delete")
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_close_response("cancel")

        def resp(_d, r):
            if r != "delete":
                return
            snap = self.store.snapshot()
            was_active = self.active_profile == name
            self.store.delete(name)
            self._after_profile_change(None)
            t = Adw.Toast(title=GLib.markup_escape_text(f"Deleted “{name}”"), button_label="Undo", timeout=6)

            def undo(*_):
                self.store.restore(snap)
                if was_active:
                    self.active_profile = name
                self._after_profile_change(None)
            t.connect("button-clicked", undo)
            self.toasts.add_toast(t)
        d.connect("response", resp)
        d.present(self)

    def export_profile(self, name):
        fd = Gtk.FileDialog(title="Export Profile", initial_name=f"{name}.json")

        def done(_fd, res):
            try:
                f = fd.save_finish(res)
            except GLib.Error:
                return
            try:
                self.store.export(name, f.get_path())
                self.toast(f"Exported to {os.path.basename(f.get_path())}")
            except OSError as e:
                self.alert("Export failed", [str(e)])
        fd.save(self, None, done)

    def import_profile(self):
        flt = Gtk.FileFilter(name="Profile (JSON)")
        flt.add_pattern("*.json")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(flt)
        fd = Gtk.FileDialog(title="Import Profile", filters=filters)

        def done(_fd, res):
            try:
                f = fd.open_finish(res)
            except GLib.Error:
                return
            try:
                name = self.store.import_file(f.get_path())
            except (OSError, ValueError) as e:
                self.alert("Import failed", [str(e)])
                return
            self._refresh_profiles()
            self.toast(f"Imported “{name}”")
        fd.open(self, None, done)

    # ---- misc
    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))

    def alert(self, heading, items, notes=()):
        body = "\n".join(f"• {GLib.markup_escape_text(i)}" for i in items)
        if notes:
            body += "\n\n" + "\n".join(f"<small>{GLib.markup_escape_text(n)}</small>" for n in notes)
        d = Adw.AlertDialog(heading=heading, body=body, body_use_markup=True)
        d.add_response("ok", "OK")
        d.present(self)

    def open_folder(self):
        os.makedirs(core.CONFIG_DIR, exist_ok=True)
        Gtk.FileLauncher(file=Gio.File.new_for_path(core.CONFIG_DIR)).launch(self, None, None)

    def about(self):
        d = Adw.AboutDialog(application_name=core.APP_NAME, version=core.VERSION, application_icon=core.APP_ICON,
                            developer_name="Kenan Güler", license_type=Gtk.License.MIT_X11,
                            comments="Per-power-source CPU, platform, device and battery tuning for laptops, "
                                     "built on TLP and the Linux power interfaces.")
        d.add_credit_section("Built with", ["TLP", "GTK 4", "libadwaita"])
        d.present(self)

    def shortcuts(self):
        d = Adw.AlertDialog(heading="Keyboard Shortcuts", body_use_markup=True, body=(
            "<b>Ctrl+Enter</b>   Apply settings\n<b>Ctrl+S</b>   Save profile\n"
            "<b>Ctrl+Shift+S</b>   Save profile as\n<b>F5 / Ctrl+R</b>   Reload from system\n"
            "<b>Alt+1…5</b>   Switch page\n<b>Ctrl+Q</b>   Quit"))
        d.add_response("ok", "Close")
        d.present(self)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=core.APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        GLib.set_application_name(core.APP_NAME)

    def do_startup(self):
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        data = os.path.join(os.path.dirname(core.PKG_DIR), "data")
        if os.path.isdir(data):  # running from a checkout: icon is not installed
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(data)
        Gtk.Window.set_default_icon_name(core.APP_ICON)
        q = Gio.SimpleAction.new("quit", None)
        q.connect("activate", lambda *_: self.quit())
        self.add_action(q)
        for action, accels in (("app.quit", ["<Ctrl>q"]), ("win.apply", ["<Ctrl>Return"]),
                               ("win.reload", ["F5", "<Ctrl>r"]), ("win.save-profile", ["<Ctrl>s"]),
                               ("win.save-profile-as", ["<Ctrl><Shift>s"])):
            self.set_accels_for_action(action, accels)

    def do_activate(self):
        win = self.props.active_window or MainWindow(self)
        for i, name in enumerate(("overview", "settings", "profiles", "battery", "system"), 1):
            ctl = Gtk.ShortcutController(scope=Gtk.ShortcutScope.GLOBAL)
            ctl.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string(f"<Alt>{i}"),
                                          action=Gtk.CallbackAction.new(
                                              lambda *_a, n=name: win.stack.set_visible_child_name(n) or True)))
            win.add_controller(ctl)
        win.present()


def main():
    return App().run([])
