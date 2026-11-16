import re
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

try:
    from rice import gui
except Exception as e:  # no gi / Gtk
    gui = None
    SKIP = str(e)

from rice.state import Status


def comp(noctalia=False):
    steps = [{"op": "noctalia"}] if noctalia else []
    return NS(steps=steps, variants={})


@unittest.skipIf(gui is None, "needs PyGObject")
class AppearanceState(unittest.TestCase):
    def st(self, **kw):
        return Status(enabled=True, **kw)

    def test_rules_in_order(self):
        c = comp(True)
        f = gui.appearance_state
        self.assertEqual(f(c, self.st(), "on")[0], "Will be applied")
        self.assertEqual(f(c, self.st(), "off")[0], "Will be turned off")
        # pending beats everything else
        self.assertEqual(f(c, self.st(app_missing=True, missing_needs=["x"]), "on")[0], "Will be applied")
        self.assertEqual(f(c, self.st(app_missing=True, missing_needs=["x"]), None)[0], "App not installed")
        self.assertEqual(f(c, self.st(missing_needs=["x"], modified=["a"]), None)[0], "Needs packages")
        self.assertEqual(f(c, Status(enabled=False), None)[0], "Off")
        self.assertEqual(f(c, self.st(modified=["a"], run_pending=True), None)[0], "Hand-edited")
        self.assertEqual(f(c, self.st(run_pending=True, needs_apply=True), None)[0], "Waiting for Noctalia")
        self.assertEqual(f(comp(), self.st(run_pending=True, needs_apply=True), None)[0], "Needs apply")
        self.assertEqual(f(c, self.st(needs_apply=None), None)[0], "Not built yet")
        self.assertEqual(f(c, self.st(recipe_changed=True), None)[0], "Needs apply")
        self.assertEqual(f(c, self.st(stale_sources=["a"]), None)[0], "Needs apply")
        self.assertEqual(f(c, self.st(), None), ("Applied ✓", "success"))


@unittest.skipIf(gui is None, "needs PyGObject")
class Pure(unittest.TestCase):
    def test_needs_ui(self):
        pst = {"qt-theme": NS(missing=["kvantum"])}
        self.assertEqual(gui.needs_ui(Status(), pst), (None, False))
        self.assertEqual(gui.needs_ui(None, pst), (None, False))
        self.assertEqual(gui.needs_ui(Status(missing_needs=["qt-theme"]), pst), ("Needs: kvantum", True))
        self.assertEqual(gui.needs_ui(Status(missing_needs=["x"]), pst), ("Needs: x", True))

    def test_pending(self):
        p = gui.pending_state
        self.assertEqual(p(Status(enabled=False), True, None), "on")
        self.assertEqual(p(Status(enabled=True), False, None), "off")
        self.assertIsNone(p(Status(enabled=True), True, None))
        self.assertEqual(p(Status(enabled=True, needs_apply=True), True, None), "on")
        self.assertEqual(p(Status(enabled=True, variant="a"), True, "b"), "on")
        self.assertIsNone(p(Status(enabled=False, missing_needs=["x"]), True, None))  # excluded from on
        self.assertIsNone(p(Status(enabled=False), False, None))
        self.assertEqual(gui.pending_count(["on", None, "off"]), 2)

    def test_summary_and_package_state(self):
        self.assertEqual(gui.summary(3, None), "3 pending · upstream updates: not checked")
        self.assertEqual(gui.summary(0, True), "0 pending · upstream updates: yes")
        self.assertEqual(gui.summary(0, False), "0 pending · upstream updates: no")
        self.assertEqual(gui.package_state(NS(state="installed", missing=[]))[0], "Installed ✓")
        self.assertEqual(gui.package_state(NS(state="missing", missing=["a"]))[0], "Not installed")
        self.assertEqual(gui.package_state(NS(state="partial", missing=["a", "b"]))[0],
                         "Partially installed: missing a, b")

    def test_poll_outcome(self):
        R = lambda rc, failed=(): NS(rc=rc, failed=list(failed))  # noqa: E731
        self.assertEqual(gui.poll_outcome("running", None), (False, None))
        self.assertEqual(gui.poll_outcome("cancelled", None), (True, "Cancelled"))
        self.assertEqual(gui.poll_outcome("no-start", None), (True, "Terminal did not start"))
        self.assertEqual(gui.poll_outcome("done", R(0)), (True, "Done"))
        self.assertEqual(gui.poll_outcome("done", R(1, ["micro"])), (True, "Failed: micro"))

    def test_no_cyrillic_in_gui_source(self):
        src = (Path(gui.__file__)).read_text()
        self.assertIsNone(re.search("[Ѐ-ӿ]", src))


@unittest.skipIf(gui is None, "needs PyGObject")
class RefreshQueue(unittest.TestCase):
    def make(self):
        class W(gui.Runner):
            def __init__(self):
                self.refreshes = 0
                self.logs = []

            def log(self, m):
                self.logs.append(m)

            def refresh(self):
                self.refreshes += 1
                super().refresh()

            def package_status(self):
                return {}
        return W()

    def run_sync(self):
        class T:
            def __init__(self, target, daemon=None):
                self.target = target

            def start(self):
                self.target()
        return mock.patch.multiple(gui, threading=mock.Mock(Thread=T),
                                   GLib=mock.Mock(idle_add=lambda f, *a: f(*a)))

    def test_refresh_while_busy_is_queued_then_runs_once(self):
        w = self.make()
        w.busy = True
        w.refresh()
        w.refresh()
        self.assertTrue(w.refresh_wanted)
        self.assertEqual(w.refreshes, 2)
        w.busy = False
        w.refreshes = 0
        w.refresh_wanted = True
        done = []
        with self.run_sync(), mock.patch.object(gui, "_cli", return_value=NS(status=lambda: {"a": 1})):
            w.on_refreshed = lambda res: done.append(res)
            w.run_bg(lambda: "x", done.append)
        self.assertEqual(done[0], "x")
        self.assertEqual(w.refreshes, 1)   # the queued refresh ran exactly once
        self.assertFalse(w.refresh_wanted)
        self.assertFalse(w.busy)
        self.assertEqual(done[1], ({"a": 1}, {}))


if __name__ == "__main__":
    unittest.main()
