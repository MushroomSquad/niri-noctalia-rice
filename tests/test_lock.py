import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from rice import lock

FIX = Path(__file__).parent / "fixtures" / "pkgbuilds"
COMMIT = "3d68043b106a6fa34ce7f8fbe96349105556bdcd"


def read(pkg):
    return (FIX / pkg / "PKGBUILD").read_text()


class TestLock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lk = lock.make_lock(COMMIT, read)

    def test_parse_mokka(self):
        p = lock.parse_pkgbuild(read("garuda-mokka"))
        self.assertEqual(len(p["source"]), 9)
        self.assertEqual(len(p["sha256sums"]), 9)
        self.assertNotIn("depends", p)  # indented, inside package()

    def test_mokka(self):
        e = self.lk["garuda-mokka"]
        self.assertEqual(e["ref"], "7e0a1c7503a79454782d082fda38f8af89be608f")
        self.assertTrue(e["sha256"].startswith("d0089ec5"))
        self.assertEqual(e["kind"], "gitlab-archive")

    def test_colloid(self):
        e = self.lk["colloid"]
        self.assertEqual(e["kind"], "git")
        self.assertTrue(e["ref"].startswith("fd805dbe"))
        self.assertNotIn("sha256", e)

    def test_done(self):
        e = self.lk["done"]
        self.assertIn("/1.21.1/", e["url"])
        self.assertTrue(e["sha256"].startswith("402a83d9"))

    def test_config_fish(self):
        e = self.lk["garuda-fish-config"]
        self.assertEqual(e["kind"], "pkgbuilds-path")
        self.assertTrue(e["sha256"].startswith("23f4d025"))
        self.assertIn(f"/raw/{COMMIT}/garuda-fish-config/config.fish", e["url"])

    def test_wallpapers(self):
        self.assertTrue(self.lk["garuda-wallpapers"]["ref"].startswith("a0f80847"))

    def test_indented_lines_ignored(self):
        text = ("pkgname=x\nsource=(a\n  b)\nsha256sums=('1'\n  '2')\n"
                "package() {\n  depends=(zzz)\n  install -D a b\n}\nsource=(late)\n")
        p = lock.parse_pkgbuild(text)
        self.assertEqual([s for s, _ in p["source"]], ["a", "b"])
        self.assertNotIn("depends", p)

    def test_expansion_and_unquoted_git(self):
        p = lock.parse_pkgbuild("_c='abc'\nsource=(\"u/${_c}/$_c\"\n  git+https://h/o/r#commit=ff\n)\nsha256sums=('1'\n 'SKIP')\n")
        self.assertEqual([s for s, _ in p["source"]], ["u/abc/abc", "git+https://h/o/r#commit=ff"])
        self.assertEqual(lock.classify("p", "git+https://h/o/r#commit=ff", "SKIP", "c")["url"], "https://h/o/r.git")

    def test_length_mismatch(self):
        with self.assertRaises(AssertionError):
            lock.parse_pkgbuild("source=(a b)\nsha256sums=('1')\n")

    def test_lock_roundtrip_and_moved(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(lock, "LOCK_PATH", Path(d) / "mokka" / "l.toml"), \
                mock.patch.object(lock, "_get", lambda url: read(url.split("/")[-2])):
            moved = lock.lock(COMMIT)
            self.assertEqual(len(moved), len(lock.IDS))
            self.assertEqual(tomllib.loads(lock.LOCK_PATH.read_text()), self.lk)
            self.assertEqual(lock.lock(COMMIT, write=False), {})


class TestRealLock(unittest.TestCase):
    def test_committed_lock(self):
        d = tomllib.loads((Path(__file__).parent.parent / "mokka" / "upstream.lock.toml").read_text())
        self.assertTrue(d["garuda-mokka"]["ref"].startswith("7e0a1c7"))
        self.assertEqual(d, lock.make_lock(COMMIT, read))


if __name__ == "__main__":
    unittest.main()
