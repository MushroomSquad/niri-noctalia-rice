"""Data checks over every recipe in mokka/components."""
import tomllib
import unittest
from pathlib import Path

from rice import cli, recipe

ROOT = Path(__file__).parent.parent
BUILTIN = Path("/usr/share/noctalia/assets/templates/builtin.toml")
COLOR_KEYS = ("ColorScheme", "LookAndFeelPackage")


def _all_steps(c):
    return c.steps + (c.off_steps or []) + [s for v in c.variants.values() for s in v.steps]


class RecipesData(unittest.TestCase):
    def setUp(self):
        self.comps = [recipe.load(p) for p in sorted(cli.COMPONENTS_DIR.glob("*.toml"))]
        self.lock = tomllib.loads((ROOT / "mokka" / "upstream.lock.toml").read_text())

    def test_loads_and_unique_ids(self):
        self.assertTrue(self.comps)
        ids = [c.id for c in self.comps]
        self.assertEqual(len(ids), len(set(ids)))

    def test_source_ids_exist_in_lock(self):
        for c in self.comps:
            refs = list(c.sources)
            for s in _all_steps(c):
                refs += [s[k] for k in ("src", "target") if isinstance(s.get(k), str) and ":" in s[k]]
                if s["op"] == "run" and "src" in s:
                    refs.append(s["src"] + ":")
            for r in refs:
                sid = r.partition(":")[0]
                if sid != "repo":
                    self.assertIn(sid, self.lock, f"{c.id}: unknown source {sid}")

    def test_repo_sources_exist(self):
        for c in self.comps:
            for s in _all_steps(c):
                if s["op"] == "copy" and s["src"].startswith("repo:"):
                    self.assertTrue((ROOT / s["src"][5:]).exists(), f"{c.id}: {s['src']}")

    def test_qt_no_color_keys(self):
        for c in self.comps:
            if c.id not in ("qt", "kde-apps"):
                continue
            for s in _all_steps(c):
                if s["op"] == "ini_set":
                    self.assertFalse(s["section"].startswith("Colors:"), f"{c.id}: {s}")
                    self.assertNotIn(s["key"], COLOR_KEYS, f"{c.id}: {s}")

    @unittest.skipUnless(BUILTIN.is_file(), "noctalia builtin.toml not installed")
    def test_builtin_ids_exist(self):
        known = set(tomllib.loads(BUILTIN.read_text())["templates"])
        for c in self.comps:
            for s in _all_steps(c):
                if s["op"] == "toml_set" and s["key"].endswith("builtin_ids") and isinstance(s["value"], dict):
                    (kind, i), = s["value"].items()
                    if kind == "add":
                        self.assertIn(i, known, f"{c.id}: {i}")


if __name__ == "__main__":
    unittest.main()
