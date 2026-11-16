"""The `noctalia` op, against a fake `noctalia` executable first on PATH (never the live shell)."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rice import cli, state
from rice import lock as lock_mod

FAKE = '''#!/usr/bin/env python3
import json, os, sys, tomllib
f = os.environ["FAKE_NOCTALIA"]
d = json.load(open(f))
a = sys.argv[2:]
d["calls"].append(a)
rc, out = 0, ""
if not d["running"]:
    print("Error: noctalia is not running", file=sys.stderr); rc = 1
elif a[0] == "color-scheme-get":
    out = d["scheme"]
elif a[0] == "color-scheme-set":
    d["scheme"] = " ".join(a[1:])
elif a[0] == "config-get":
    if a[1] in d["config"]:
        out = "[" + ", ".join("'%s'" % x for x in d["config"][a[1]]) + "]"
    else:
        print("error: path not found: " + a[1], file=sys.stderr); rc = 1
elif a[0] == "config-set":
    d["config"][a[1]] = tomllib.loads("v = " + a[2])["v"]
json.dump(d, open(f, "w"))
print(out)
sys.exit(rc)
'''
IDS = "theme.templates.builtin_ids"
COMP = f'''id="pal"
title="P"
group="look"
[[steps]]
op="toml_set"
target=".config/noctalia/config.toml"
key="theme.custom_palette"
value="Mokka"
[[steps]]
op="noctalia"
color_scheme=["custom","Mokka"]
[[steps]]
op="noctalia"
list_add="{IDS}"
item="gtk3"
[[steps]]
op="noctalia"
list_remove="{IDS}"
item="kde"
[[off_steps]]
op="noctalia"
color_scheme=["custom","Catppuccin-Lavender"]
[[off_steps]]
op="noctalia"
list_remove="{IDS}"
item="gtk3"
[[off_steps]]
op="toml_set"
target=".config/noctalia/config.toml"
key="theme.custom_palette"
value="Catppuccin-Lavender"
'''


class NoctaliaOp(unittest.TestCase):
    def setUp(self):
        t = Path(tempfile.mkdtemp())
        self.home = t / "home"
        (self.home / ".config/noctalia").mkdir(parents=True)
        self.cfg = self.home / ".config/noctalia/config.toml"
        self.cfg.write_text('[theme]\nsource = "custom"\ncustom_palette = "Orig"\n')
        bin_ = t / "bin"
        bin_.mkdir()
        (bin_ / "noctalia").write_text(FAKE)
        (bin_ / "noctalia").chmod(0o755)
        self.fake = t / "fake.json"
        self.set_fake(running=True, scheme="custom Orig", config={IDS: ["a", "kde"]}, calls=[])
        comps = t / "components"
        comps.mkdir()
        (comps / "pal.toml").write_text(COMP)
        (t / "lock.toml").write_text("")
        env = mock.patch.dict(os.environ, {"HOME": str(self.home), "XDG_CACHE_HOME": str(t / "cache"),
                                           "XDG_STATE_HOME": str(t / "state"), "FAKE_NOCTALIA": str(self.fake),
                                           "PATH": f"{bin_}:{os.environ['PATH']}"})
        env.start()
        self.addCleanup(env.stop)
        for p in (mock.patch.object(cli, "COMPONENTS_DIR", comps), mock.patch.object(lock_mod, "LOCK_PATH", t / "lock.toml")):
            p.start()
            self.addCleanup(p.stop)

    def set_fake(self, **kw):
        d = json.loads(self.fake.read_text()) if self.fake.exists() else {}
        self.fake.write_text(json.dumps({**d, **kw}))

    def fk(self):
        return json.loads(self.fake.read_text())

    def calls(self, name=None):
        return [c for c in self.fk()["calls"] if name is None or c[0] == name]

    def apply(self, **kw):
        return cli.apply(["pal"], packages=False, log=lambda *_: None, **kw)["pal"]

    def test_get_before_set_and_idempotent(self):
        r = self.apply()
        self.assertEqual(r.status, "applied")
        self.assertEqual(self.calls("config-reload"), [["config-reload"]])
        self.assertEqual(self.calls("color-scheme-set"), [["color-scheme-set", "custom", "Mokka"]])
        self.assertEqual(self.fk()["config"][IDS], ["a", "gtk3"])
        # forced re-run (pending) with everything already in place: gets only, 0 sets
        st = state.load()
        st["pal"]["run_pending"] = True
        state.save(st)
        self.set_fake(calls=[])
        self.apply()
        self.assertTrue(self.calls("color-scheme-get") and self.calls("config-get"))
        self.assertEqual(self.calls("color-scheme-set") + self.calls("config-set"), [])
        self.set_fake(calls=[])
        self.assertEqual(self.apply().status, "unchanged")
        self.assertEqual(self.calls(), [])

    def test_dry_run_and_status_make_no_calls(self):
        r = self.apply(dry_run=True)
        self.assertIn("run", [c.kind for c in r.changes])
        self.assertTrue(any(c.relpath.startswith("noctalia color_scheme") for c in r.changes))
        cli.status()
        self.assertEqual(self.calls(), [])
        self.apply(run=False)
        self.assertEqual(self.calls(), [])
        self.assertTrue(state.load()["pal"]["run_pending"])
        cli.status()
        self.assertEqual(self.calls(), [])

    def test_not_running_then_retry(self):
        self.set_fake(running=False)
        r = self.apply()
        self.assertEqual(r.status, "failed")
        self.assertTrue(state.load()["pal"]["run_pending"])
        self.set_fake(running=True)
        self.assertNotEqual(self.apply().status, "failed")
        self.assertFalse(state.load()["pal"]["run_pending"])
        self.assertEqual(self.fk()["scheme"], "custom Mokka")

    def test_missing_binary_is_pending(self):
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}):
            self.assertEqual(self.apply().status, "failed")
        self.assertTrue(state.load()["pal"]["run_pending"])

    def test_prev_present_recorded(self):
        self.apply()
        prev = state.load()["pal"]["prev"]
        self.assertEqual(prev["noctalia:color_scheme"], ["custom", "Orig"])
        self.assertIs(prev[f"noctalia:list:{IDS}:gtk3"], False)
        self.assertIs(prev[f"noctalia:list:{IDS}:kde"], False)  # config.toml has no list: absent

    def test_disable_restores_original(self):
        self.apply()
        r = cli.disable(["pal"], log=lambda *_: None)["pal"]
        self.assertEqual(r.status, "applied")
        self.assertEqual(self.fk()["scheme"], "custom Orig")
        self.assertEqual(self.fk()["config"][IDS], ["a"])
        self.assertIn('custom_palette = "Orig"', self.cfg.read_text())

    def test_disable_keeps_item_that_was_present(self):
        self.cfg.write_text(self.cfg.read_text() + f'[theme.templates]\nbuiltin_ids = ["gtk3"]\n')
        self.set_fake(config={IDS: ["gtk3"]})
        self.apply()
        self.assertIs(state.load()["pal"]["prev"][f"noctalia:list:{IDS}:gtk3"], True)
        cli.disable(["pal"], log=lambda *_: None)
        self.assertIn("gtk3", self.fk()["config"][IDS])

    def test_disable_not_running_stays_enabled(self):
        self.apply()
        before = self.cfg.read_text()
        self.set_fake(running=False)
        r = cli.disable(["pal"], log=lambda *_: None)["pal"]
        self.assertEqual(r.status, "failed")
        self.assertIn("not running", r.message)
        self.assertTrue(state.is_enabled(state.load(), "pal"))
        self.assertEqual(self.cfg.read_text(), before)
        self.assertIn("noctalia:color_scheme", state.load()["pal"]["prev"])
        self.set_fake(running=True)
        self.assertEqual(cli.disable(["pal"], log=lambda *_: None)["pal"].status, "applied")

    def test_missing_list_is_empty(self):
        self.set_fake(config={})
        self.apply()
        self.assertEqual(self.fk()["config"][IDS], ["gtk3"])


if __name__ == "__main__":
    unittest.main()
