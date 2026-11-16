import re
import tempfile
import tomllib
import unittest
from pathlib import Path

from rice import recipe

FX = Path(__file__).parent / "fixtures" / "recipe"
ROOT = Path(__file__).parent.parent


def op(kind, **kw):
    return {"op": kind, **kw}


def twice(text, o):
    once = recipe.patch(text, o, "/h")
    assert recipe.patch(once, o, "/h") == once, "not idempotent"
    return once


class Patches(unittest.TestCase):
    def test_replace(self):
        o = op("replace", target="x", pattern=r"radius 20 //", repl="radius 16 $HOME //")
        self.assertEqual(twice("radius 20 // a\n", o), "radius 16 /h //\n".replace("//\n", "// a\n"))

    def test_drop_lines_real_fish(self):
        text = (FX / "config.fish").read_text()
        o = op("drop_lines", target="x", pattern=r"^alias (upd|fixpacman|rmpkg|mirror\w*|grubup)\s")
        out = twice(text, o)
        self.assertIn("rmpkg", text)
        self.assertIsNone(re.search(r"rmpkg|fixpacman|grubup|^alias mirror", out, re.M))
        self.assertIn("alias hw", out)

    def test_ini_set_keeps_other_lines(self):
        text = (FX / "kdeglobals").read_text()
        out = twice(text, op("ini_set", target="x", section="General", key="font", value="Inter,10"))
        old, new = text.splitlines(), out.splitlines()
        self.assertEqual([l for l in new if l != "font=Inter,10"], old)
        out = twice(text, op("ini_set", target="x", section="General", key="Name", value="x"))
        self.assertIn("Name=x\n", out)
        self.assertEqual(len(out.splitlines()), len(text.splitlines()))

    def test_ini_set_missing_file_and_section(self):
        o = op("ini_set", target="x", section="Settings", key="k", value="v")
        self.assertEqual(twice("", o), "[Settings]\nk=v\n")
        out = twice("[A]\na=1", op("ini_set", target="x", section="B", key="k", value=2))
        self.assertEqual(out, "[A]\na=1\n\n[B]\nk=2\n")

    def test_ini_set_kconfig_key_suffix(self):
        out = twice("[A]\nk[$e]=old\n", op("ini_set", target="x", section="A", key="k", value="new"))
        self.assertEqual(out, "[A]\nk[$e]=new\n")

    def test_toml_set_literal_real_config(self):
        text = (FX / "noctalia-config.toml").read_text()
        out = twice(text, op("toml_set", target="x", key="theme.custom_palette", value="Mokka"))
        self.assertEqual(tomllib.loads(out)["theme"]["custom_palette"], "Mokka")
        a, b = text.splitlines(), out.splitlines()
        self.assertEqual([x for x in a if "custom_palette" not in x], [x for x in b if "custom_palette" not in x])
        out = twice(text, op("toml_set", target="x", key="shell.font_family", value="Inter"))
        self.assertEqual(tomllib.loads(out)["shell"]["font_family"], "Inter")

    def test_toml_set_builtin_ids_add_remove(self):
        text = (FX / "noctalia-config.toml").read_text()
        ids = tomllib.loads(text)["theme"]["templates"]["builtin_ids"]
        k = "theme.templates.builtin_ids"
        for v in ({"remove": "gtk3"}, {"remove": "starship"}, {"add": "gtk3"}):
            text = twice(text, op("toml_set", target="x", key=k, value=v))
        got = tomllib.loads(text)["theme"]["templates"]["builtin_ids"]
        self.assertEqual(got, [i for i in ids if i != "starship"] + ["gtk3"])
        text = twice(text, op("toml_set", target="x", key=k, value={"add": "kcolorscheme"}))
        self.assertEqual(tomllib.loads(text)["theme"]["templates"]["builtin_ids"][-1], "kcolorscheme")

    def test_toml_set_creates(self):
        o = op("toml_set", target="x", key="theme.a", value=True)
        self.assertEqual(tomllib.loads(twice("", o)), {"theme": {"a": True}})
        out = twice("[t]\nx = 1\n\n[u]\ny = 2\n", op("toml_set", target="x", key="t.z", value="s"))
        self.assertEqual(tomllib.loads(out), {"t": {"x": 1, "z": "s"}, "u": {"y": 2}})
        out = twice("top = 1\n[t]\nx = 1\n", op("toml_set", target="x", key="n", value=5))
        self.assertEqual(tomllib.loads(out)["n"], 5)
        out = twice("", op("toml_set", target="x", key="t.l", value={"add": "a"}))
        self.assertEqual(tomllib.loads(out), {"t": {"l": ["a"]}})

    def test_json_set(self):
        o = op("json_set", target="x", key="a.b", value="v")
        self.assertEqual(twice("", o), '{\n  "a": {\n    "b": "v"\n  }\n}\n')
        import json
        self.assertEqual(json.loads(twice('{"z": 1}', o)), {"z": 1, "a": {"b": "v"}})


class Loader(unittest.TestCase):
    def write(self, body):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d))
        p = d / "c.toml"
        p.write_text(body)
        return p

    HEAD = 'id = "c"\ntitle = "C"\ngroup = "look"\n'

    def test_load_ok(self):
        c = recipe.load(self.write(self.HEAD + '''
default_variant = "a"
[[steps]]
op = "copy"
src = "repo:x"
dst = ".x"
[variants.a]
title = "A"
[[variants.a.steps]]
op = "ini_set"
target = "t"
section = "s"
key = "k"
value = "v"
[variants.b]
'''))
        self.assertFalse(c.reversible)
        self.assertEqual(set(c.variants), {"a", "b"})
        self.assertNotEqual(recipe.recipe_hash(c, "a"), recipe.recipe_hash(c, "b"))
        self.assertEqual(recipe.recipe_hash(c, "a"), recipe.recipe_hash(c))
        self.assertEqual(list(recipe.patch_ops(c, "a")), ["t"])
        self.assertEqual(recipe.patch_ops(c, "b"), {})

    def test_reversible_with_off_steps(self):
        c = recipe.load(self.write(self.HEAD + '[[steps]]\nop="replace"\ntarget="t"\npattern="a"\nrepl="b"\n'
                                   '[[off_steps]]\nop="replace"\ntarget="t"\npattern="b"\nrepl="a"\n'))
        self.assertTrue(c.reversible)

    def test_errors_name_file_and_field(self):
        bad = {
            "group": 'id="c"\ntitle="C"\ngroup="nope"\n',
            "steps[0].op": self.HEAD + '[[steps]]\nop="zap"\n',
            "steps[0].src": self.HEAD + '[[steps]]\nop="copy"\nsrc="nocolon"\ndst="x"\n',
            "steps[0].dst": self.HEAD + '[[steps]]\nop="copy"\nsrc="repo:x"\n',
            "default_variant": self.HEAD + 'default_variant="q"\n[variants.a]\n',
            "steps[0].when": self.HEAD + '[[steps]]\nop="run"\nargv=["x"]\nwhen="never"\n',
        }
        for field, body in bad.items():
            p = self.write(body)
            with self.assertRaises(ValueError, msg=field) as cm:
                recipe.load(p)
            self.assertIn(str(p), str(cm.exception))
            self.assertIn(field, str(cm.exception))


class Render(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        self.src = self.tmp / "src"
        (self.src / "d").mkdir(parents=True)
        (self.src / "d" / "a.txt").write_text("A\n")
        (self.src / "d" / "lock.json").write_text("{}")
        (self.src / "run.sh").write_text("#!/bin/sh\necho clean_theme && x\nmkdir -p \"$1\" && echo built > \"$1/out.txt\"\n")
        (self.src / "run.sh").chmod(0o755)

    def comp(self, steps):
        d = {"id": "c", "title": "C", "description": "", "group": "look", "default": False, "conflicts": [],
             "requires": [], "needs": [], "only_if_installed": None, "previews": [], "sources": [],
             "steps": steps, "off_steps": None, "niri_snippet": None, "variants": {}, "default_variant": None,
             "path": Path("x")}
        return recipe.Component(**d)

    def render(self, steps, gens=None):
        return recipe.render_whole(self.comp(steps), None, {"up": self.src}, self.tmp / "repo",
                                   self.tmp / "out", gens or {})

    def test_copy_dir_and_seed(self):
        w = self.render([op("copy", src="up:d", dst=".cfg/d"),
                         op("copy", src="up:d/lock.json", dst=".cfg/d/lock.json", track=False)])
        self.assertEqual({k: v[1] for k, v in w.items()}, {".cfg/d/a.txt": True, ".cfg/d/lock.json": False})
        self.assertEqual(w[".cfg/d/a.txt"][0].read_text(), "A\n")

    def test_copy_file_into_dir(self):
        self.assertIn(".x/a.txt", self.render([op("copy", src="up:d/a.txt", dst=".x/")]))

    def test_generate(self):
        def gen(sources, out):
            p = out / ".g" / "p.json"
            p.parent.mkdir(parents=True)
            p.write_text("{}")
            return {".g/p.json": p}
        w = self.render([op("generate", generator="g")], {"g": gen})
        self.assertEqual(set(w), {".g/p.json"})

    def test_copy_plus_patch_hashes_patched_text(self):
        (self.src / "config.fish").write_text((FX / "config.fish").read_text())
        w = self.render([
            op("copy", src="up:config.fish", dst=".config/fish/config.fish"),
            op("drop_lines", target=".config/fish/config.fish", pattern=r"^alias (upd|fixpacman|rmpkg|mirror\w*|grubup)\s"),
        ])
        path, track = w[".config/fish/config.fish"]
        text = path.read_text()
        self.assertTrue(track)
        self.assertIsNone(re.search(r"rmpkg|fixpacman|grubup|^alias mirror", text, re.M))
        self.assertNotEqual(text, (FX / "config.fish").read_text())
        self.assertEqual((self.src / "config.fish").read_text(), (FX / "config.fish").read_text())

    def test_build_run_on_patched_scratch(self):
        w = self.render([
            op("replace", target="up:run.sh", pattern=r"echo clean_theme && x", repl=":"),
            op("run", when="build", src="up", argv=["./run.sh", "{out}/.themes"]),
            op("run", when="apply", argv=["false"]),
        ])
        self.assertEqual({k: v[1] for k, v in w.items()}, {".themes/out.txt": True})
        self.assertIn("clean_theme", (self.src / "run.sh").read_text())  # source cache untouched

    def test_live_patch_target_not_staged(self):
        c = self.comp([op("toml_set", target=".config/x.toml", key="a", value=1)])
        self.assertEqual(recipe.render_whole(c, None, {}, self.tmp, self.tmp / "o", {}), {})
        self.assertEqual(list(recipe.patch_ops(c)), [".config/x.toml"])


if __name__ == "__main__":
    unittest.main()
