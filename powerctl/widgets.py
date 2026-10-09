"""Reusable GTK widgets."""
from collections import deque

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk, Pango  # noqa: E402

from . import core  # noqa: E402
from .core import COLUMN_TITLES, COLUMNS, SECTIONS  # noqa: E402

CSS = """
.stat-card { padding: 14px 16px; }
.stat-value { font-size: 20pt; font-weight: 800; }
.stat-unit { font-size: 11pt; font-weight: 600; opacity: 0.7; }
.hero { padding: 20px 24px; }
.hero-value { font-size: 34pt; font-weight: 800; }
.chart-card { padding: 14px 16px 10px 16px; }
.matrix-row { padding: 6px 12px; min-height: 52px; }
.matrix-header { padding: 0 12px; }
.cell { padding: 4px 10px; border-radius: 8px; }
.cell.active-col { background-color: alpha(@accent_bg_color, 0.10); }
.col-title { font-weight: 700; }
.pill { border-radius: 99px; padding: 1px 9px; font-size: 8pt; font-weight: 800; }
.pill.accent { background-color: @accent_bg_color; color: @accent_fg_color; }
.pill.warning { background-color: alpha(@warning_bg_color, 0.85); color: @warning_fg_color; }
.pill.success { background-color: alpha(@success_bg_color, 0.85); color: @success_fg_color; }
.pill.neutral { background-color: alpha(currentColor, 0.12); }
.profile-bar { padding: 10px 14px; }
.section-title { font-weight: 700; margin-top: 18px; margin-bottom: 6px; }
.preview { font-family: monospace; font-size: 9.5pt; padding: 12px; background: transparent; }
.threshold-bar trough { min-height: 14px; border-radius: 7px; }
.threshold-bar block { border-radius: 7px; }
"""

COLW = 250
ACCENT = (0.21, 0.52, 0.89)
ORANGE = (0.90, 0.38, 0.0)
GREEN = (0.15, 0.64, 0.41)


def pill(text, style="accent"):
    lbl = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
    lbl.add_css_class("pill")
    lbl.add_css_class(style)
    return lbl


def dim(text, *classes, **kw):
    kw.setdefault("xalign", 0)
    lbl = Gtk.Label(label=text, **kw)
    lbl.add_css_class("dim-label")
    for c in classes:
        lbl.add_css_class(c)
    return lbl


class StatCard(Gtk.Box):
    def __init__(self, icon, title):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("card")
        self.add_css_class("stat-card")
        top = Gtk.Box(spacing=8)
        top.append(Gtk.Image(icon_name=icon, css_classes=["dim-label"]))
        top.append(dim(title, "caption-heading"))
        self.append(top)
        val = Gtk.Box(spacing=4, valign=Gtk.Align.BASELINE)
        self.value = Gtk.Label(label="-", xalign=0, css_classes=["stat-value", "numeric"])
        self.unit = Gtk.Label(label="", xalign=0, valign=Gtk.Align.BASELINE, css_classes=["stat-unit"])
        val.append(self.value)
        val.append(self.unit)
        self.append(val)
        self.sub = dim("", "caption", ellipsize=Pango.EllipsizeMode.END)
        self.append(self.sub)

    def set(self, value, unit="", sub=""):
        self.value.set_text(value)
        self.unit.set_text(unit)
        self.sub.set_text(sub)


class Sparkline(Gtk.DrawingArea):
    def __init__(self, color, points=150, floor=1.0):
        super().__init__(content_height=110, hexpand=True)
        self.color, self.floor = color, floor
        self.data = deque(maxlen=points)
        self.set_draw_func(self._draw)

    def push(self, v):
        if v is not None:
            self.data.append(v)
            self.queue_draw()

    def _draw(self, _area, cr, w, h):
        fg = self.get_color()
        cr.set_line_width(1)
        for i in range(4):
            y = 0.5 + round(i * (h - 1) / 3)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.10)
            cr.move_to(0, y)
            cr.line_to(w, y)
            cr.stroke()
        if len(self.data) < 2:
            return
        top = max(max(self.data) * 1.15, self.floor)
        n = self.data.maxlen
        step = w / (n - 1)
        x0 = w - (len(self.data) - 1) * step
        pts = [(x0 + i * step, h - 2 - (v / top) * (h - 6)) for i, v in enumerate(self.data)]
        r, g, b = self.color
        cr.move_to(pts[0][0], h)
        for x, y in pts:
            cr.line_to(x, y)
        cr.line_to(pts[-1][0], h)
        cr.close_path()
        cr.set_source_rgba(r, g, b, 0.16)
        cr.fill()
        cr.set_line_width(2)
        cr.set_source_rgba(r, g, b, 1)
        cr.move_to(*pts[0])
        for x, y in pts[1:]:
            cr.line_to(x, y)
        cr.stroke()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.55)
        cr.select_font_face("Sans")
        cr.set_font_size(10)
        cr.move_to(4, 12)
        cr.show_text(f"{top:.0f}")


class ChartCard(Gtk.Box):
    def __init__(self, title, color, unit, fmt="{:.1f}", floor=1.0):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        self.add_css_class("card")
        self.add_css_class("chart-card")
        self.unit, self.fmt = unit, fmt
        head = Gtk.Box(spacing=8)
        dot = Gtk.DrawingArea(content_width=10, content_height=10, valign=Gtk.Align.CENTER)
        dot.set_draw_func(lambda _a, cr, w, h: (cr.set_source_rgb(*color), cr.arc(w / 2, h / 2, 5, 0, 6.3),
                                                cr.fill()))
        head.append(dot)
        head.append(Gtk.Label(label=title, xalign=0, hexpand=True, css_classes=["heading"]))
        self.cur = Gtk.Label(label="-", css_classes=["numeric", "heading"])
        head.append(self.cur)
        self.append(head)
        self.chart = Sparkline(color, floor=floor)
        self.append(self.chart)
        self.append(dim("Last 5 minutes", "caption"))

    def push(self, v):
        self.chart.push(v)
        self.cur.set_text("-" if v is None else f"{self.fmt.format(v)} {self.unit}")


class MatrixEditor(Gtk.Box):
    """Settings table with one column for AC and one for battery."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.cells, self.widgets = {c: [] for c in COLUMNS}, {}
        self._loading = False
        self.sizegroup = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self.settings = core.available_settings()
        for section, desc in SECTIONS:
            items = [s for s in self.settings if s.section == section]
            if not items:
                continue
            head = Gtk.Box(spacing=8, css_classes=["section-title"])
            head.append(Gtk.Label(label=section, xalign=0, css_classes=["heading"]))
            head.append(dim(desc, "caption"))
            self.append(head)
            lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
            for s in items:
                lb.append(self._row(s))
            self.append(lb)

    def header(self):
        box = Gtk.Box(spacing=12, css_classes=["matrix-header"])
        box.append(dim("Setting", "caption-heading", hexpand=True, margin_start=1))
        self.headers = {}
        for c, icon in (("ac", "ac-adapter-symbolic"), ("bat", "battery-symbolic")):
            h = Gtk.Box(spacing=8, width_request=COLW, hexpand=False, css_classes=["cell"])
            self.sizegroup.add_widget(h)
            h.append(Gtk.Image(icon_name=icon))
            h.append(Gtk.Label(label=COLUMN_TITLES[c], css_classes=["col-title"]))
            p = pill("ACTIVE")
            h.append(p)
            self.headers[c] = (h, p)
            self.cells[c].append(h)
            box.append(h)
        return box

    def _row(self, s):
        row = Gtk.ListBoxRow(activatable=False, selectable=False)
        box = Gtk.Box(spacing=12, css_classes=["matrix-row"])
        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER, spacing=2)
        txt.append(Gtk.Label(label=s.title, xalign=0))
        txt.append(dim(s.subtitle, "caption", wrap=True))
        box.append(txt)
        for c in COLUMNS:
            cell = Gtk.Box(width_request=COLW, valign=Gtk.Align.FILL, hexpand=False, css_classes=["cell"])
            self.sizegroup.add_widget(cell)
            w = self._widget(s, c)
            w.set_valign(Gtk.Align.CENTER)
            cell.append(w)
            self.cells[c].append(cell)
            self.widgets[(c, s.key)] = w
            box.append(cell)
        row.set_child(box)
        return row

    def _widget(self, s, col):
        if s.kind == "choice":
            vals = s.choices()
            w = Gtk.DropDown.new_from_strings([s.label(v) for v in vals])
            w.values = vals
            w.set_hexpand(True)
            w.connect("notify::selected", lambda *_: self._changed(col, s.key))
        elif s.kind == "bool":
            w = Gtk.Switch(halign=Gtk.Align.START)
            w.connect("notify::active", lambda *_: self._changed(col, s.key))
        else:
            adj = Gtk.Adjustment(lower=s.lo, upper=s.hi, step_increment=1, page_increment=10)
            w = Gtk.Scale(adjustment=adj, digits=0, draw_value=True, hexpand=True,
                          value_pos=Gtk.PositionType.RIGHT)
            w.set_format_value_func(lambda _s, v: f"{v:3.0f} %")
            for m in (25, 50, 75):
                w.add_mark(m, Gtk.PositionType.BOTTOM, None)
            w.connect("value-changed", lambda *_: self._changed(col, s.key))
        return w

    # ---- values
    def _get(self, col, key):
        w, s = self.widgets[(col, key)], core.SMAP[key]
        if s.kind == "choice":
            i = w.get_selected()
            return w.values[i] if 0 <= i < len(w.values) else None
        if s.kind == "bool":
            return s.on if w.get_active() else s.off
        return str(int(round(w.get_value())))

    def _set(self, col, key, v):
        w, s = self.widgets[(col, key)], core.SMAP[key]
        if s.kind == "choice":
            if v in w.values:
                w.set_selected(w.values.index(v))
        elif s.kind == "bool":
            w.set_active(v == s.on)
        else:
            w.set_value(int(v))

    def get_values(self):
        out = {c: {} for c in COLUMNS}
        for (c, k) in self.widgets:
            v = self._get(c, k)
            if v is not None:
                out[c][k] = v
        return out

    def set_values(self, values, emit=True):
        self._loading = True
        try:
            for c in COLUMNS:
                for k, v in (values.get(c) or {}).items():
                    if (c, k) in self.widgets:
                        self._set(c, k, v)
                self._sync_epp(c)
        finally:
            self._loading = False
        if emit:
            self.emit("changed")

    def set_active_column(self, col):
        for c in COLUMNS:
            for cell in self.cells[c]:
                (cell.add_css_class if c == col else cell.remove_css_class)("active-col")
            self.headers[c][1].set_visible(c == col)

    def _sync_epp(self, col):
        if (col, "epp") not in self.widgets or (col, "governor") not in self.widgets:
            return
        w = self.widgets[(col, "epp")]
        forced = core.pstate_active() and self._get(col, "governor") == "performance"
        if forced and "performance" in w.values:
            w.set_selected(w.values.index("performance"))
        w.set_sensitive(not forced)
        w.set_tooltip_text("intel_pstate forces the Performance policy with the performance governor"
                           if forced else None)

    def _changed(self, col, key):
        if self._loading:
            return
        self._loading = True
        try:
            if key == "governor":
                self._sync_epp(col)
            if key in ("min_perf", "max_perf") and (col, "min_perf") in self.widgets \
                    and (col, "max_perf") in self.widgets:
                lo, hi = int(self._get(col, "min_perf")), int(self._get(col, "max_perf"))
                if lo > hi:
                    self._set(col, "max_perf" if key == "min_perf" else "min_perf", lo if key == "min_perf" else hi)
        finally:
            self._loading = False
        self.emit("changed")


class AdwRowHelper:
    @staticmethod
    def prop(title, value=""):
        r = Adw.ActionRow(title=title, subtitle=value, css_classes=["property"])
        r.set_subtitle_selectable(True)
        return r
