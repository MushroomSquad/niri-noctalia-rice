import hashlib
import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rice import fetch

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def make_tar(d: Path) -> tuple[Path, str]:
    p = d / "src.tar.gz"
    with tarfile.open(p, "w:gz") as t:
        data = b"hello\n"
        ti = tarfile.TarInfo("pkg-1/b.txt")
        ti.size = len(data)
        t.addfile(ti, io.BytesIO(data))
    return p, hashlib.sha256(p.read_bytes()).hexdigest()


def make_repo(d: Path, texts) -> list[str]:
    d.mkdir()
    subprocess.run(GIT + ["init", "-q"], cwd=d, check=True)
    refs = []
    for t in texts:
        (d / "a.txt").write_text(t)
        subprocess.run(GIT + ["add", "."], cwd=d, check=True)
        subprocess.run(GIT + ["commit", "-qm", t], cwd=d, check=True)
        refs.append(subprocess.run(["git", "rev-parse", "HEAD"], cwd=d, check=True,
                                   capture_output=True, text=True).stdout.strip())
    return refs


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        p = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.tmp / "cache")})
        p.start()
        self.addCleanup(p.stop)

    def test_archive_good_and_bad_sha(self):
        tgz, sha = make_tar(self.tmp)
        e = {"url": tgz.as_uri(), "kind": "gitlab-archive", "ref": "r1", "sha256": sha}
        d = fetch.ensure("a", e)
        self.assertEqual((d / "b.txt").read_text(), "hello\n")
        self.assertEqual(fetch.ensure("a", e), d)  # cached
        with self.assertRaises(ValueError):
            fetch.ensure("b", dict(e, sha256="0" * 64))
        self.assertIsNone(fetch.path("b", e))

    def test_raw_file(self):
        f = self.tmp / "done.fish"
        f.write_text("x")
        e = {"url": f.as_uri(), "kind": "raw-file", "ref": "1.0", "sha256": hashlib.sha256(b"x").hexdigest()}
        self.assertEqual(fetch.ensure("done", e).name, "done.fish")

    def test_git_by_commit(self):
        refs = make_repo(self.tmp / "repo", ["one", "two"])
        e = {"url": (self.tmp / "repo").as_uri(), "kind": "git", "ref": refs[0]}
        d = fetch.ensure("g", e)
        self.assertEqual((d / "a.txt").read_text(), "one")
        self.assertFalse((d / ".git").exists())

    def test_git_missing(self):
        with mock.patch("rice.fetch.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "git is required to fetch g"):
                fetch.ensure("g", {"url": "x", "kind": "git", "ref": "r"})


if __name__ == "__main__":
    unittest.main()
