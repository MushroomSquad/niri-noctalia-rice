"""GTK4/libadwaita front-end: a thin layer over rice/cli.py and rice/packages.py (PLAN-2 §4)."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path.home() / ".cache/niri-noctalia-rice"
COLORIZED_NOTE = {"colloid": "runs upstream build script"}
TERMINAL_NOTE = "Opens a terminal, needs sudo"
APPEARANCE_GROUPS = {
    "look": ("Look & palette", "preferences-desktop-appearance-symbolic"),
    "qt": ("Qt & KDE apps", "applications-system-symbolic"),
    "gtk": ("GTK", "applications-graphics-symbolic"),
    "shell": ("Shell", "utilities-terminal-symbolic"),
    "terminal": ("Terminal apps", "utilities-terminal-symbolic"),
    "apps": ("App configs", "applications-other-symbolic"),
    "wallpapers": ("Wallpapers", "preferences-desktop-wallpaper-symbolic"),
}
PACKAGE_GROUPS = {
    "base": ("Base system", "computer-symbolic"),
    "theme": ("Theme packages", "preferences-desktop-theme-symbolic"),
    "apps": ("Applications", "applications-other-symbolic"),
    "dev": ("Development", "applications-engineering-symbolic"),
}
STATE_CSS = ("success", "warning", "accent", "dim-label")


# ---- pure helpers (unit-tested without a display)
def appearance_state(c, s, pending):
    """First matching rule of PLAN-2 §4.4 -> (text, css class)."""
    if pending == "on":
        return "Will be applied", "accent"
    if pending == "off":
        return "Will be turned off", "accent"
    if s is None:
        return "Off", "dim-label"
    if s.app_missing:
        return "App not installed", "dim-label"
    if s.missing_needs:
        return "Needs packages", "warning"
    if not s.enabled:
        return "Off", "dim-label"
    if s.modified:
        return "Hand-edited", "warning"
    steps = list(c.steps) + [x for v in c.variants.values() for x in v.steps]
    if s.run_pending and any(x.get("op") == "noctalia" for x in steps):
        return "Waiting for Noctalia", "warning"
    if s.needs_apply is None:
        return "Not built yet", "dim-label"
    if s.needs_apply or s.recipe_changed or s.stale_sources:
        return "Needs apply", "accent"
    return "Applied ✓", "success"


def needs_ui(s, pst=None):
    """(label|None, show_button): built only from the live missing needs, never from stale text."""
    missing = list(s.missing_needs) if s else []
    if not missing:
        return None, False
    names = []
    for i in missing:
        names += (pst[i].missing if pst and i in pst and pst[i].missing else [i])
    return "Needs: " + ", ".join(dict.fromkeys(names)), True


def pending_state(s, active, variant):
    """'on' | 'off' | None for one component given the switch and variant picker."""
    was = bool(s and s.enabled)
    if active and s and s.missing_needs:
        return None
    if active and (not was or (s and s.needs_apply) or (variant and s and variant != s.variant)):
        return "on"
    if not active and was:
        return "off"
    return None


def pending_count(pendings):
    return sum(1 for p in pendings if p)


def summary(n, updates):
    u = "not checked" if updates is None else "yes" if updates else "no"
    return f"{n} pending · upstream updates: {u}"


def package_state(st):
    """SetStatus -> (text, css)."""
    if st is None:
        return "Unknown", "dim-label"
    if st.state == "installed":
        return "Installed ✓", "success"
    if st.state == "partial":
        return "Partially installed: missing " + ", ".join(st.missing), "warning"
    return "Not installed", "dim-label"


def poll_outcome(state, res):
    """Map a packages.poll_state result (+ InstallResult on done) -> (finished, toast)."""
    if state == "done":
        if res is None or res.rc:
            return True, "Failed: " + ", ".join(res.failed) if res else "Failed"
        return True, "Done"
    if state == "cancelled":
        return True, "Cancelled"
    if state == "no-start":
        return True, "Terminal did not start"
    return False, None


# ---- model helpers
def _cli():
    from rice import cli
    return cli


def load_components():
    return list(_cli().components())


def preview_files(c):
    files = sorted((ROOT / "mokka/previews" / c.id).glob("*.png"))
    for ref in getattr(c, "previews", []):  # "<lock-id>:<path>"
        lid, _, rel = ref.partition(":")
        base = _cli().source_path(lid)
        p = base if base and base.is_file() else base / rel if base else None
        if p and p.is_file():
            files.append(p)
    return files


def palette_colors(cid):
    for p in [CACHE / "build" / cid / ".config/noctalia/palettes/Mokka.json"]:
        try:
            d = json.loads(p.read_text())
            d = d.get("dark", d)
            cols = [v for v in d.values() if isinstance(v, str) and v.startswith("#")]
        except (OSError, ValueError, AttributeError):
            continue
        if cols:
            return cols
    return []


def swatch_row(cols):
    """A row of color blocks drawn on one DrawingArea (no per-color CSS)."""
    rgba = []
    for col in cols:
        c = Gdk.RGBA()
        if c.parse(col):
            rgba.append(c)
    area = Gtk.DrawingArea(height_request=28, hexpand=True)

    def draw(_a, cr, w, h):
        gap, n = 3, max(len(rgba), 1)
        bw = (w - gap * (n - 1)) / n
        for i, c in enumerate(rgba):
            cr.set_source_rgba(c.red, c.green, c.blue, 1)
            cr.rectangle(i * (bw + gap), 0, bw, h)
            cr.fill()
    area.set_draw_func(draw)
    return area


CSS = b"""
.preview-bg { background: black; }
.card-title { font-weight: 700; font-size: 110%; }
"""


class Pic(Gtk.Picture):
    """Picture whose natural width is the card width, not the image width (keeps the FlowBox 3-wide)."""
    height = 220

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, 360, -1, -1
        return self.height, self.height, -1, -1


class Runner:
    """Background-work queue; refresh requested while busy is queued, never lost (PLAN-2 §4.5)."""
    busy = False
    refresh_wanted = False

    def run_bg(self, fn, done):
        if self.busy:
            return False
        self.busy = True

        def work():
            try:
                res = fn()
            except Exception as e:  # noqa: BLE001 - surface any failure in the log, never crash the UI
                self.log(f"error: {e}")
                res = None
            GLib.idle_add(finish, res)

        def finish(res):
            self.busy = False
            done(res)
            if self.refresh_wanted:
                self.refresh_wanted = False
                self.refresh()
            return False
        threading.Thread(target=work, daemon=True).start()
        return True

    def refresh(self):
        if self.busy:
            self.refresh_wanted = True
            return
        self.run_bg(lambda: (_cli().status(), self.package_status()), self.on_refreshed)

    def package_status(self):
        from rice import packages
        return packages.status()

    def on_refreshed(self, res):
        if res is not None:
            self.status, self.pst = res
            self.sync()


class Window(Runner, Adw.ApplicationWindow):
    def __init__(self, app, tab="appearance"):
        super().__init__(application=app, title="niri-noctalia-rice", default_width=1200, default_height=820)
        from rice import packages
        self.packages = packages
        self.cards = {}      # appearance: id -> dict of widgets
        self.pcards = {}     # package set id -> dict of widgets
        self.sections = {"appearance": [], "packages": []}
        self.pictures = []
        self.status, self.pst = {}, {}
        self.updates = None
        self.term_active = False
        self.reset_switches = True
        self.comps, self.sets = [], []

        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.stack = Adw.ViewStack()
        self.stack.add_titled_with_icon(self.page(), "appearance", "Appearance", "applications-graphics-symbolic")
        self.stack.add_titled_with_icon(self.page(), "packages", "Packages", "package-x-generic-symbolic")
        self.stack.set_visible_child_name(tab)

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        for icon, tip, fn in (("view-refresh-symbolic", "Check for upstream updates", self.on_check),
                              ("folder-download-symbolic", "Fetch sources", self.on_fetch),
                              ("folder-open-symbolic", "Open custom folder", self.on_open_custom)):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.connect("clicked", fn)
            header.pack_start(b)
        self.apply_btn = Gtk.Button(label="Apply (0)", css_classes=["suggested-action"], sensitive=False)
        self.apply_btn.connect("clicked", self.on_apply)
        header.pack_end(self.apply_btn)
        diff = Gtk.Button(label="Show changes")
        diff.connect("clicked", self.on_diff)
        header.pack_end(diff)
        self.summary_lbl = Gtk.Label(label=summary(0, None), css_classes=["dim-label"])
        header.pack_end(self.summary_lbl)

        self.log_view = Gtk.TextView(editable=False, monospace=True, cursor_visible=False)
        self.log_exp = Gtk.Expander(label="Log", child=Gtk.ScrolledWindow(child=self.log_view, min_content_height=140))
        bar = Adw.ViewSwitcherBar(stack=self.stack)
        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bottom.append(bar)
        bottom.append(self.log_exp)

        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(header)
        view.add_bottom_bar(bottom)
        self.toasts = Adw.ToastOverlay(child=view)
        self.set_content(self.toasts)
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 600sp"))
        bp.add_setter(bar, "reveal", True)
        self.add_breakpoint(bp)

        self.build()
        self.refresh()

    # ---- layout
    def page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=24, margin_bottom=24,
                      margin_start=24, margin_end=24)
        clamp = Adw.Clamp(maximum_size=2000, tightening_threshold=1600, child=box)
        sc = Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        sc.box = box
        return sc

    def section(self, tab, title, icon, expander=None):
        box = self.stack.get_child_by_name(tab).box
        head = Gtk.Box(spacing=8)
        head.append(Gtk.Image(icon_name=icon))
        head.append(Gtk.Label(label=title, css_classes=["title-2"]))
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, min_children_per_line=1,
                           max_children_per_line=4, column_spacing=18, row_spacing=18, valign=Gtk.Align.START)
        if expander:
            ex = Gtk.Expander(label=expander, expanded=False, child=flow)
            box.append(ex)
        else:
            sec = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            sec.append(head)
            sec.append(flow)
            box.append(sec)
        return flow

    def build(self):
        self.comps = load_components()
        self.sets = self.packages.load()
        by_group = {}
        for c in self.comps:
            by_group.setdefault(c.group, []).append(c)
        for g in [*APPEARANCE_GROUPS, *[k for k in by_group if k not in APPEARANCE_GROUPS]]:
            if g in by_group:
                title, icon = APPEARANCE_GROUPS.get(g, (g.title(), "applications-other-symbolic"))
                flow = self.section("appearance", title, icon)
                for c in by_group[g]:
                    flow.append(self.appearance_card(c))
        for g, (title, icon) in PACKAGE_GROUPS.items():
            sets = [s for s in self.sets if s.group == g]
            if sets:
                flow = self.section("packages", title, icon, "Base system (always installed)" if g == "base" else None)
                for s in sets:
                    flow.append(self.package_card(s))

    def card_frame(self, files, height, empty="Fetch to see preview"):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["card"], width_request=360,
                       overflow=Gtk.Overflow.HIDDEN)
        if height:
            card.append(self.carousel(files, height, empty))
        return card

    def carousel(self, files, height, empty):
        car = Adw.Carousel(height_request=height)
        for f in files:
            pic = Pic()
            pic.height = height
            pic.set_filename(str(f))
            pic.set_hexpand(True)
            pic.set_content_fit(Gtk.ContentFit.COVER)
            self.pictures.append(pic)
            car.append(pic)
        if not files:
            car.append(Gtk.Label(label=empty, css_classes=["dim-label"]))
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: files and self.open_fullscreen(files, round(car.get_position())))
        car.add_controller(click)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(car)
        if len(files) > 1:
            box.append(Adw.CarouselIndicatorDots(carousel=car))
        return box

    def body(self, card, title, desc):
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=12)
        body.append(Gtk.Label(label=title, xalign=0, wrap=True, max_width_chars=36, css_classes=["card-title"]))
        body.append(Gtk.Label(label=desc or "", xalign=0, wrap=True, max_width_chars=36, lines=2, ellipsize=3, css_classes=["dim-label"]))
        card.append(body)
        return body

    def appearance_card(self, c):
        card = self.card_frame(preview_files(c), 204)
        if c.id == "palette" and (cols := palette_colors(c.id)):
            sw = swatch_row(cols)
            sw.set_margin_start(12)
            sw.set_margin_end(12)
            sw.set_margin_top(8)
            card.append(sw)
        body = self.body(card, c.title, c.description)
        w = {"c": c, "state": Gtk.Label(xalign=0, wrap=True, max_width_chars=36)}
        body.append(w["state"])
        w["combo"] = None
        if c.variants:
            names = list(c.variants)
            w["combo"] = Gtk.DropDown(model=Gtk.StringList.new([c.variants[n].title for n in names]))
            w["combo"].connect("notify::selected", lambda *_: self.sync())
            body.append(w["combo"])
        w["needs"] = Gtk.Label(xalign=0, wrap=True, max_width_chars=36, css_classes=["warning"], visible=False)
        w["install"] = Gtk.Button(label="Install packages", css_classes=["suggested-action"], visible=False,
                                  halign=Gtk.Align.START)
        w["install"].connect("clicked", lambda *_: self.run_terminal("install", list(self.status[c.id].missing_needs)))
        w["note"] = Gtk.Label(label=TERMINAL_NOTE, xalign=0, css_classes=["dim-label", "caption"], visible=False)
        for x in (w["needs"], w["install"], w["note"]):
            body.append(x)
        foot = Gtk.Box(spacing=8, margin_top=6)
        foot.append(Gtk.Label(label="Enabled", hexpand=True, xalign=0))
        w["switch"] = Gtk.Switch(valign=Gtk.Align.CENTER)
        w["switch"].connect("notify::active", lambda *_: self.sync())
        foot.append(w["switch"])
        body.append(foot)
        if not c.reversible:
            body.append(Gtk.Label(label="Turning off keeps current settings", xalign=0,
                                  css_classes=["dim-label", "caption"]))
        self.cards[c.id] = w
        return card

    def package_card(self, s):
        files = sorted((ROOT / "mokka/previews" / s.id).glob("*.png"))
        card = self.card_frame(files, 204, "No preview")
        body = self.body(card, s.title, s.description)
        names = "repo: " + " ".join(s.repo) if s.repo else ""
        if s.aur:
            names += (" · " if names else "") + "AUR: " + " ".join(s.aur)
        body.append(Gtk.Label(label=names, xalign=0, wrap=True, max_width_chars=36, css_classes=["dim-label", "caption"]))
        w = {"set": s, "state": Gtk.Label(xalign=0, wrap=True, max_width_chars=36)}
        body.append(w["state"])
        row = Gtk.Box(spacing=8)
        w["install"] = Gtk.Button(label="Install missing" if s.group == "base" else "Install",
                                  css_classes=["suggested-action"])
        w["install"].connect("clicked", lambda *_: self.run_terminal("install", [s.id]))
        row.append(w["install"])
        w["remove"] = Gtk.Button(label="Remove", css_classes=["destructive-action"])
        w["remove"].connect("clicked", lambda *_: self.confirm_remove(s))
        row.append(w["remove"])
        body.append(row)
        body.append(Gtk.Label(label=TERMINAL_NOTE, xalign=0, css_classes=["dim-label", "caption"]))
        self.pcards[s.id] = w
        return card

    # ---- state -> widgets
    def selection(self):
        on, off, variants = [], [], {}
        for cid, w in self.cards.items():
            p = self.pending_of(cid)
            v = self.variant_of(cid)
            if p == "on":
                on.append(cid)
                if v:
                    variants[cid] = v
            elif p == "off":
                off.append(cid)
        return on, off, variants

    def variant_of(self, cid):
        w = self.cards[cid]
        return list(w["c"].variants)[w["combo"].get_selected()] if w["combo"] else None

    def pending_of(self, cid):
        return pending_state(self.status.get(cid), self.cards[cid]["switch"].get_active(), self.variant_of(cid))

    @staticmethod
    def set_state(label, text, css):
        for k in STATE_CSS:
            label.remove_css_class(k)
        label.add_css_class(css)
        label.set_label(text)

    def sync(self):
        """Update labels, buttons and the header in place; the user's pending toggles survive."""
        if getattr(self, "_syncing", False):
            return
        self._syncing = True
        try:
            for cid, w in self.cards.items():
                s, c = self.status.get(cid), w["c"]
                if self.reset_switches:
                    w["switch"].set_active(bool(s.enabled) if s else c.default)
                    if w["combo"]:
                        names = list(c.variants)
                        cur = (s.variant if s and s.variant in names else c.default_variant) or names[0]
                        w["combo"].set_selected(names.index(cur))
                text, css = appearance_state(c, s, self.pending_of(cid))
                self.set_state(w["state"], text, css)
                label, show = needs_ui(s, self.pst)
                w["needs"].set_visible(bool(label))
                w["needs"].set_label(label or "")
                w["install"].set_visible(show)
                w["install"].set_sensitive(not self.term_active)
                w["note"].set_visible(show)
                w["switch"].set_sensitive(not (s and s.missing_needs and not s.enabled))
            self.reset_switches = False
            for sid, w in self.pcards.items():
                st = self.pst.get(sid)
                text, css = package_state(st)
                self.set_state(w["state"], text, css)
                installed = bool(st and st.state == "installed")
                w["install"].set_visible(not installed)
                w["install"].set_sensitive(not self.term_active)
                w["remove"].set_visible(w["set"].removable and installed)
                w["remove"].set_sensitive(not self.term_active)
            n = pending_count(self.pending_of(cid) for cid in self.cards)
            self.apply_btn.set_label(f"Apply ({n})")
            self.apply_btn.set_sensitive(n > 0)
            self.summary_lbl.set_label(summary(n, self.updates))
        finally:
            self._syncing = False

    # ---- fullscreen preview
    def open_fullscreen(self, files, index):
        win = Gtk.Window(transient_for=self, modal=True, css_classes=["preview-bg"])
        car = Adw.Carousel()
        for f in files:
            pic = Gtk.Picture.new_for_filename(str(f))
            pic.set_content_fit(Gtk.ContentFit.CONTAIN)
            car.append(pic)
        win.set_child(car)
        keys = Gtk.EventControllerKey()

        def key(_c, kv, *_):
            pos = round(car.get_position())
            if kv == Gdk.KEY_Escape:
                win.close()
            elif kv in (Gdk.KEY_Left, Gdk.KEY_Right):
                nxt = max(0, min(len(files) - 1, pos + (1 if kv == Gdk.KEY_Right else -1)))
                car.scroll_to(car.get_nth_page(nxt), True)
            return True
        keys.connect("key-pressed", key)
        win.add_controller(keys)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: win.close())
        win.add_controller(click)
        win.fullscreen()
        win.present()
        GLib.idle_add(lambda: (car.scroll_to(car.get_nth_page(index), False), False)[-1])
        return win

    # ---- terminal runs (packages install/remove)
    def toast(self, msg):
        self.toasts.add_toast(Adw.Toast(title=msg))

    def confirm_remove(self, s):
        dlg = Adw.AlertDialog(heading=f"Remove {s.title}?", body="This runs pacman -Rns in a terminal.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("remove", "Remove")
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "remove" and self.run_terminal("remove", [s.id]))
        dlg.present(self)

    def run_terminal(self, action, ids):
        if self.term_active:
            return
        base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        result = Path(base) / f"rice-pkg-{uuid.uuid4().hex}.json"
        try:
            argv = self.packages.terminal_argv(action, ids, result)
        except FileNotFoundError:
            self.toast("No terminal found: set $TERMINAL or install xdg-terminal-exec")
            return
        self.log("$ " + " ".join(argv))
        try:
            subprocess.Popen(argv, start_new_session=True)
        except OSError as e:
            self.log(f"error: {e}")
            self.toast("Terminal did not start")
            return
        self.term_active = True
        self.sync()
        launched = time.time()
        GLib.timeout_add(500, self.poll_terminal, result, launched, action)

    def poll_terminal(self, result, launched, action):
        state = self.packages.poll_state(result, launched, time.time())
        res = self.packages.read_result(result) if state == "done" else None
        finished, text = poll_outcome(state, res)
        if not finished:
            return True
        if state == "done":
            if res and res.message:
                self.log(res.message)
            if res and not res.rc:
                text = "Installed" if action == "install" else "Removed"
            if res and res.rc:
                self.log_exp.set_expanded(True)
            result.unlink(missing_ok=True)
        elif state == "no-start":
            self.log("terminal produced no result file")
            self.log_exp.set_expanded(True)
        self.toast(text)
        self.term_active = False
        self.refresh()
        self.sync()
        return False

    # ---- other actions
    def log(self, msg):
        def add():
            buf = self.log_view.get_buffer()
            buf.insert(buf.get_end_iter(), str(msg) + "\n")
            return False
        GLib.idle_add(add)

    def on_check(self, *_):
        def work():
            return _cli().status(remote=True)

        def done(st):
            if st is None:
                return
            self.status = st
            self.updates = any(s.update_available for s in st.values())
            for cid, s in st.items():
                if s.update_available:
                    self.log(f"update available: {cid}")
            self.log("update check done")
            self.sync()
        self.log("checking for updates...")
        self.run_bg(work, done)

    def on_open_custom(self, *_):
        def work():
            folder = Path.home() / ".config/rice-custom"
            folder.mkdir(parents=True, exist_ok=True)
            subprocess.run(["xdg-open", str(folder)], check=True)
        self.run_bg(work, lambda _r: None)

    def on_fetch(self, *_):
        ids = [cid for cid, w in self.cards.items() if w["switch"].get_active()]
        self.log(f"fetching {len(ids)} component(s)...")
        self.run_bg(lambda: _cli().fetch(ids), lambda _r: self.log("fetch finished"))

    def on_diff(self, *_):
        on, off, variants = self.selection()

        def work():
            res = _cli().apply(on, variants=variants, dry_run=True, log=self.log) if on else {}
            lines = []
            for cid, r in res.items():
                lines.append(f"== {cid}: {'not built yet (Fetch/Apply builds it)' if r.status == 'skipped' and 'not built' in r.message else r.status + ' ' + r.message}")
                for ch in r.changes:
                    lines.append(f"-- {ch.kind}: {ch.relpath}")
                    if ch.diff:
                        lines.append(ch.diff)
            lines += [f"== {cid}: will be turned off" for cid in off]
            return "\n".join(lines) or "0 changes"
        self.run_bg(work, self.show_text)

    def show_text(self, text):
        if text is None:
            return
        dlg = Adw.Dialog(title="Changes", content_width=800, content_height=600)
        tv = Gtk.TextView(editable=False, monospace=True)
        tv.get_buffer().set_text(text)
        view = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=tv))
        view.add_top_bar(Adw.HeaderBar())
        dlg.set_child(view)
        dlg.present(self)

    def on_apply(self, *_):
        on, off, variants = self.selection()

        def work():
            res = {}
            if on:
                res.update(_cli().apply(on, variants=variants, log=self.log))
            if off:
                res.update(_cli().disable(off, log=self.log))
            return "\n".join(f"{cid}: {r.status} {r.message}" for cid, r in res.items()) or "nothing to do"

        def done(text):
            self.reset_switches = True
            self.show_text(text)
        self.log("applying...")
        self.run_bg(work, done)
        self.refresh_wanted = True  # refresh as soon as Apply finishes


class App(Adw.Application):
    def __init__(self, smoke=False, tab="appearance"):
        super().__init__(application_id="io.github.niri_noctalia_rice")
        self.smoke, self.tab, self.code = smoke, tab, 0

    def do_activate(self):
        win = Window(self, self.tab)
        win.present()
        if self.smoke:
            GLib.timeout_add(1500, self.run_smoke, win)

    def run_smoke(self, win):
        def fail(msg):
            print("smoke: " + msg, file=sys.stderr)
            self.code = 1
        win.sync()
        for tab in ("appearance", "packages"):
            win.stack.set_visible_child_name(tab)
            box = win.stack.get_child_by_name(tab).box
            if not any(p.get_paintable() is not None and p.is_ancestor(win.stack.get_child_by_name(tab))
                       for p in win.pictures):
                fail(f"{tab}: no picture loaded")
            if not (win.cards if tab == "appearance" else win.pcards):
                fail(f"{tab}: no cards")
            if box.get_first_child() is None:
                fail(f"{tab}: empty page")
        if win.pictures:
            fs = win.open_fullscreen([next(iter((ROOT / "mokka/previews").glob("*/*.png")))], 0)
            fs.close()
        self.quit()
        return False


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    smoke = "--smoke" in argv
    tab = argv[argv.index("--tab") + 1] if "--tab" in argv and argv.index("--tab") + 1 < len(argv) else "appearance"
    if not Gtk.init_check():
        print("gui: no display available, skipping", file=sys.stderr)
        return 0 if smoke else 1
    app = App(smoke, tab)
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # Ctrl+C in the terminal closes the window without a traceback
    app.run([])
    return app.code


if __name__ == "__main__":
    sys.exit(main())
