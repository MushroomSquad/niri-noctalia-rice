import os
import tempfile
import unittest
from pathlib import Path

from rice import state


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.stage = Path(self.tmp.name) / "stage"
        self.stage.mkdir()
        self.home.mkdir()

    def stage_file(self, name, text, track=True):
        p = self.stage / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return {name: (p, track)}

    def baks(self):
        return list(self.home.rglob("*.bak-*"))

    def test_roundtrip_and_owned(self):
        os.environ["XDG_STATE_HOME"] = self.tmp.name
        self.addCleanup(os.environ.pop, "XDG_STATE_HOME")
        self.assertEqual(state.owned(state.load()), [])
        st = {"a": {"whole": {"x": "1"}, "seeded": ["s"], "patched": ["p"]}}
        st["b"] = state.disabled_entry(st["a"])
        state.save(st)
        self.assertEqual(state.load(), st)
        self.assertFalse(state.is_enabled(st, "b"))
        self.assertTrue(state.is_enabled(st, "a"))
        st["a"] = state.disabled_entry(st["a"])
        self.assertEqual(state.owned(st), sorted(["x", "s", "p", state.RICE_KDL]))

    def test_second_install_unchanged_no_bak(self):
        f = self.stage_file("d/a.txt", "one\n")
        ch = state.install_whole(f, self.home, {}, "t1")
        self.assertEqual([c.kind for c in ch], ["new"])
        rec = {"d/a.txt": state.sha256(f["d/a.txt"][0])}
        self.assertEqual(state.install_whole(f, self.home, rec, "t2"), [])
        self.assertEqual(self.baks(), [])

    def test_hand_edit_needs_force(self):
        f = self.stage_file("a.txt", "one\n")
        state.install_whole(f, self.home, {}, "t1")
        rec = {"a.txt": state.sha256(f["a.txt"][0])}
        (self.home / "a.txt").write_text("hand\n")
        f = self.stage_file("a.txt", "two\n")
        ch = state.install_whole(f, self.home, rec, "t2")
        self.assertEqual([c.kind for c in ch], ["modified"])
        self.assertEqual((self.home / "a.txt").read_text(), "hand\n")
        ch = state.install_whole(f, self.home, rec, "t3", force=True)
        self.assertEqual([c.kind for c in ch], ["changed"])
        self.assertEqual((self.home / "a.txt").read_text(), "two\n")
        self.assertEqual(len(self.baks()), 1)

    def test_seed(self):
        f = self.stage_file("s.toml", "v1\n", track=False)
        self.assertEqual(state.install_whole(f, self.home, {}, "t1")[0].kind, "new")
        (self.home / "s.toml").write_text("mutated\n")
        self.assertEqual(state.install_whole(f, self.home, {}, "t2"), [])
        self.assertEqual((self.home / "s.toml").read_text(), "mutated\n")
        self.assertEqual(self.baks(), [])
        ch = state.install_whole(f, self.home, {}, "t3", reseed=True)
        self.assertEqual([c.kind for c in ch], ["changed"])
        self.assertEqual((self.home / "s.toml").read_text(), "v1\n")
        self.assertEqual(len(self.baks()), 1)

    def test_dry_run_writes_nothing(self):
        f = self.stage_file("a.txt", "one\n")
        ch = state.install_whole(f, self.home, {}, "t1", dry_run=True)
        self.assertEqual(ch[0].kind, "new")
        self.assertFalse((self.home / "a.txt").exists())

    def test_binary(self):
        p = self.stage / "b.bin"
        p.write_bytes(b"\xff\xfe\x00")
        ch = state.install_whole({"b.bin": (p, True)}, self.home, {}, "t")
        self.assertEqual(ch[0].kind, "binary")

    def test_write_patched(self):
        self.assertEqual(state.write_patched("p.ini", "a\n", self.home, "t1").kind, "new")
        self.assertIsNone(state.write_patched("p.ini", "a\n", self.home, "t2"))
        ch = state.write_patched("p.ini", "b\n", self.home, "t3")
        self.assertEqual(ch.kind, "changed")
        self.assertIn("+b", ch.diff)
        self.assertEqual(len(self.baks()), 1)


if __name__ == "__main__":
    unittest.main()
