"""GTK4/libadwaita front-end: a thin layer over the rice/cli.py API (PLAN §3)."""
import sys
import subprocess
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GROUPS = ["look", "qt", "gtk", "shell", "terminal", "apps", "wallpapers"]
CACHE = Path.home() / ".cache/niri-noctalia-rice"
COLORIZED_NOTE = {"colloid": "runs upstream build script"}


def _cli():
    from rice import cli
    return cli


def load_components():
    return list(_cli().components())


def load_status(remote=False):
    try:
        return _cli().status(remote=remote)
    except Exception:
        return {}


def preview_files(c):
    files = sorted((ROOT / "mokka/previews" / c.id).glob("*.png"))
    for ref in c.previews:  # "<lock-id>:<path>"
        lid, _, rel = ref.partition(":")
        base = _cli().source_path(lid)
        p = base if base and base.is_file() else base / rel if base else None
        if p and p.is_file():
            files.append(p)
    return files


def palette_swatch(c):
    """Colored boxes from a generated palette JSON in the build cache, if any."""
    import json
    for p in [CACHE / "build" / c.id / ".config/noctalia/palettes/Mokka.json"]:
        try:
            d = json.loads(p.read_text())
            d = d.get("dark", d)
            cols = [v for v in d.values() if isinstance(v, str) and v.startswith("#")]
        except (OSError, ValueError, AttributeError):
            continue
        if cols:
            box = Gtk.Box(spacing=4, homogeneous=True, height_request=120)
            prov = Gtk.CssProvider()
            prov.load_from_string("".join(f".sw{i}{{background:{col};border-radius:6px;}}" for i, col in enumerate(cols)))
            for i, _ in enumerate(cols):
                b = Gtk.Box()
                b.add_css_class(f"sw{i}")
                b.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                box.append(b)
            return box
    return None


def placeholder_picture():
    px = GLib.Bytes.new(bytes([0x31, 0x32, 0x44, 0xFF]) * 64 * 64)
    return Gtk.Picture.new_for_paintable(Gdk.MemoryTexture.new(64, 64, Gdk.MemoryFormat.R8G8B8A8, px, 64 * 4))


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="niri-noctalia-rice", default_width=1000, default_height=700)
        self.rows = {}      # id -> (SwitchRow, ComboRow|None, Component)
        self.pictures = []
        self.status = {}
        self.busy = False

        self.stack = Gtk.Stack()
        self.sidebar = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.pages = {}
        for g in GROUPS:
            self.sidebar.append(Gtk.Label(label=g, xalign=0, margin_top=10, margin_bottom=10, margin_start=8))
            page = Adw.PreferencesPage()
            self.pages[g] = page
            self.stack.add_named(page, g)
        self.sidebar.connect("row-selected", lambda _l, r: r and self.stack.set_visible_child_name(GROUPS[r.get_index()]))

        self.log_view = Gtk.TextView(editable=False, monospace=True, cursor_visible=False)
        log_sc = Gtk.ScrolledWindow(child=self.log_view, min_content_height=110)
        content = Adw.ToolbarView(content=self.stack)
        content.add_bottom_bar(log_sc)

        side = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=self.sidebar))
        side.add_top_bar(Adw.HeaderBar())
        top = Adw.HeaderBar()
        for label, fn in (("Check for updates", self.on_check), ("Fetch", self.on_fetch),
                          ("Open custom folder", self.on_open_custom),
                          ("Show changes", self.on_diff), ("Apply", self.on_apply)):
            b = Gtk.Button(label=label)
            b.connect("clicked", fn)
            if label == "Apply":
                b.add_css_class("suggested-action")
                top.pack_end(b)
            else:
                top.pack_start(b)
        content.add_top_bar(top)

        split = Adw.NavigationSplitView(
            sidebar=Adw.NavigationPage(child=side, title="Groups"),
            content=Adw.NavigationPage(child=content, title="Components"))
        self.set_content(split)
        self.build()
        self.sidebar.select_row(self.sidebar.get_row_at_index(0))

    # ---- model -> widgets
    def build(self):
        comps = load_components()
        self.status = load_status()
        by_group = {g: [] for g in GROUPS}
        for c in comps:
            by_group.setdefault(c.group, []).append(c)
        for g, cs in by_group.items():
            page = self.pages.get(g)
            if page is None:
                continue
            if not cs:
                empty = Adw.PreferencesGroup(title="No components in this group yet")
                ph = placeholder_picture()
                self.pictures.append(ph)
                ph.set_size_request(-1, 80)
                empty.add(ph)
                page.add(empty)
            for c in cs:
                page.add(self.card(c))

    def badges(self, c):
        st = self.status.get(c.id)
        out = []
        if st:
            if st.app_missing:
                out.append("app not installed (skipped)")
            if st.update_available:
                out.append("update available")
            if st.modified:
                out.append("edited by hand")
            if st.needs_apply:
                out.append("needs apply")
            if st.missing_needs:
                out.append("needs packages: " + ", ".join(st.missing_needs))
        if not c.reversible:
            out.append("turning off keeps current settings")
        for v in c.variants:
            if v in COLORIZED_NOTE:
                out.append(f"{v}: {COLORIZED_NOTE[v]}")
        return out

    def card(self, c):
        st = self.status.get(c.id)
        grp = Adw.PreferencesGroup(title=GLib.markup_escape_text(c.title), description=GLib.markup_escape_text(c.description or ""))
        grp.add(self.carousel(c))
        sw = Adw.SwitchRow(title="Enabled", active=bool(st.enabled) if st else c.default,
                           subtitle=GLib.markup_escape_text(" · ".join(self.badges(c))))
        grp.add(sw)
        combo = None
        if c.variants:
            names = list(c.variants)
            combo = Adw.ComboRow(title="Variant", model=Gtk.StringList.new([c.variants[n].title for n in names]))
            cur = (st.variant if st and st.variant in names else c.default_variant) or names[0]
            combo.set_selected(names.index(cur))
            grp.add(combo)
        self.rows[c.id] = (sw, combo, c)
        return grp

    def carousel(self, c):
        car = Adw.Carousel()
        items = []
        files = preview_files(c)
        for f in files:
            items.append(Gtk.Picture.new_for_filename(str(f)))
        if c.id == "palette" and (sw := palette_swatch(c)):
            items.append(sw)
        if not items:
            items.append(Gtk.Label(label="Fetch to see preview", css_classes=["dim-label"]))
            if not files:
                items.append(placeholder_picture())  # ponytail: bundled-in-code placeholder until T8 screenshots
        for it in items:
            if isinstance(it, Gtk.Picture):
                self.pictures.append(it)
                it.set_content_fit(Gtk.ContentFit.CONTAIN)
            it.set_size_request(-1, 160)
            car.append(it)
        return car

    # ---- actions (never on the main thread)
    def log(self, msg):
        def add():
            buf = self.log_view.get_buffer()
            buf.insert(buf.get_end_iter(), str(msg) + "\n")
            return False
        GLib.idle_add(add)

    def run_bg(self, fn, done):
        if self.busy:
            return
        self.busy = True

        def work():
            try:
                res = fn()
            except Exception as e:  # noqa: BLE001 - surface any failure in the log, never crash the UI
                self.log(f"error: {e}")
                res = None
            GLib.idle_add(lambda: (setattr(self, "busy", False), done(res), False)[-1])
        threading.Thread(target=work, daemon=True).start()

    def selection(self):
        on, off, variants = [], [], {}
        for cid, (sw, combo, c) in self.rows.items():
            st = self.status.get(cid)
            was = bool(st and st.enabled)
            v = list(c.variants)[combo.get_selected()] if combo else None
            if sw.get_active() and (not was or (st and st.needs_apply) or (v and st and v != st.variant)):
                on.append(cid)
                if v:
                    variants[cid] = v
            elif not sw.get_active() and was:
                off.append(cid)
        return on, off, variants

    def on_check(self, *_):
        def done(st):
            if st is not None:
                self.status = st
                for cid, s in st.items():
                    if s.update_available:
                        self.log(f"update available: {cid}")
                self.log("update check done")
        self.log("checking for updates...")
        self.run_bg(lambda: _cli().status(remote=True), done)

    def on_open_custom(self, *_):
        def work():
            folder = Path.home() / ".config/rice-custom"
            folder.mkdir(parents=True, exist_ok=True)
            subprocess.run(["xdg-open", str(folder)], check=True)
        self.run_bg(work, lambda _r: None)

    def on_fetch(self, *_):
        ids = [cid for cid, (sw, _c, _x) in self.rows.items() if sw.get_active()]
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
            self.status = load_status()
            return "\n".join(f"{cid}: {r.status} {r.message}" for cid, r in res.items()) or "nothing to do"
        self.log("applying...")
        self.run_bg(work, self.show_text)


class App(Adw.Application):
    def __init__(self, smoke=False):
        super().__init__(application_id="io.github.niri_noctalia_rice")
        self.smoke, self.code = smoke, 0

    def do_activate(self):
        win = Window(self)
        win.present()
        if self.smoke:
            GLib.idle_add(self.run_smoke, win)

    def run_smoke(self, win):
        for g in GROUPS:
            win.stack.set_visible_child_name(g)
        if not any(p.get_paintable() is not None for p in win.pictures):
            print("smoke: no picture loaded", file=sys.stderr)
            self.code = 1
        self.quit()
        return False


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    smoke = "--smoke" in argv
    if not Gtk.init_check():
        print("gui: no display available, skipping", file=sys.stderr)
        return 0 if smoke else 1
    app = App(smoke)
    app.run([])
    return app.code


if __name__ == "__main__":
    sys.exit(main())
