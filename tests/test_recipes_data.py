"""Data checks over every recipe in mokka/components."""
import subprocess
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

    def _ids(self, suffix):
        """(kind, id) of add ops on theme.templates.<suffix>."""
        for c in self.comps:
            for s in _all_steps(c):
                key = s.get("key") or s.get("list_add") or s.get("list_remove") or ""
                if key.endswith(suffix):
                    if s["op"] == "toml_set" and isinstance(s["value"], dict):
                        (kind, i), = s["value"].items()
                        if kind == "add":
                            yield c.id, i
                    elif s["op"] == "noctalia" and "list_add" in s:
                        yield c.id, s["item"]

    @unittest.skipUnless(BUILTIN.is_file(), "noctalia builtin.toml not installed")
    def test_builtin_ids_exist(self):
        known = set(tomllib.loads(BUILTIN.read_text())["templates"])
        for cid, i in self._ids("builtin_ids"):
            self.assertIn(i, known, f"{cid}: {i}")

    def test_community_ids_exist(self):
        try:
            out = subprocess.run(["noctalia", "theme", "--list-templates"], capture_output=True, text=True,
                                 timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        if "Community templates" not in out:
            self.skipTest("noctalia community template list unavailable")
        known = {l.split()[0] for l in out.split("Community templates", 1)[1].splitlines()[2:] if l.split()}
        for cid, i in self._ids("community_ids"):
            self.assertIn(i, known, f"{cid}: {i}")

if __name__ == "__main__":
    unittest.main()
